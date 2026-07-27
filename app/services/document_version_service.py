"""知识库文档版本状态机与 active-version 检索可见性管理。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.core.database import database_manager


@dataclass(frozen=True)
class PendingVersion:
    document_id: str
    version_id: str
    version_number: int
    duplicate: bool = False


class DocumentVersionService:
    """以 PostgreSQL 为版本真源；进程内快照仅用于同步检索路径过滤。"""

    def __init__(self) -> None:
        self._active_version_ids: set[str] = set()
        self._lock = RLock()

    async def refresh_active_versions(self) -> set[str]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                "SELECT active_version_id FROM knowledge_documents WHERE status = 'active' AND active_version_id IS NOT NULL"
            )
            rows = await result.fetchall()
        with self._lock:
            self._active_version_ids = {str(row[0]) for row in rows}
            return set(self._active_version_ids)

    def _activate_in_snapshot(self, version_id: str, previous_version_id: str | None = None) -> None:
        with self._lock:
            if previous_version_id:
                self._active_version_ids.discard(previous_version_id)
            self._active_version_ids.add(version_id)

    def _remove_document_versions_from_snapshot(self, version_ids: list[str]) -> None:
        with self._lock:
            self._active_version_ids.difference_update(version_ids)

    def is_active_version(self, metadata: dict) -> bool:
        """旧分片无版本字段时保持可见，避免升级后历史知识库突然为空。"""
        version_id = metadata.get("_version_id")
        if not version_id:
            return True
        with self._lock:
            return str(version_id) in self._active_version_ids

    async def create_pending_version(
        self, logical_path: str, content_hash: str, storage_path: Path, size_bytes: int, *, force: bool = False
    ) -> PendingVersion:
        pool = database_manager.require_pool()
        async with pool.connection() as connection, connection.transaction():
            # 对同一逻辑路径串行化，避免并发覆盖得到相同版本号。
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (logical_path,))
            if not force:
                duplicate_result = await connection.execute(
                    """SELECT d.document_id, d.active_version_id, v.version_number
                       FROM knowledge_documents d JOIN knowledge_document_versions v ON v.version_id = d.active_version_id
                       WHERE d.status = 'active' AND v.content_hash = %s LIMIT 1""",
                    (content_hash,),
                )
                duplicate = await duplicate_result.fetchone()
                if duplicate:
                    return PendingVersion(str(duplicate[0]), str(duplicate[1]), int(duplicate[2]), duplicate=True)
            result = await connection.execute(
                "SELECT document_id, active_version_id FROM knowledge_documents WHERE logical_path = %s FOR UPDATE",
                (logical_path,),
            )
            row = await result.fetchone()
            if row:
                document_id, active_version_id = str(row[0]), row[1]
                if active_version_id:
                    active_result = await connection.execute(
                        "SELECT content_hash, version_id, version_number FROM knowledge_document_versions WHERE version_id = %s",
                        (active_version_id,),
                    )
                    active = await active_result.fetchone()
                    if active and active[0] == content_hash and not force:
                        return PendingVersion(document_id, str(active[1]), int(active[2]), duplicate=True)
                number_result = await connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM knowledge_document_versions WHERE document_id = %s",
                    (document_id,),
                )
                version_number = int((await number_result.fetchone())[0])
            else:
                document_id = str(uuid4())
                version_number = 1
                await connection.execute(
                    "INSERT INTO knowledge_documents (document_id, logical_path, status) VALUES (%s, %s, 'active')",
                    (document_id, logical_path),
                )
            version_id = str(uuid4())
            await connection.execute(
                """INSERT INTO knowledge_document_versions
                   (version_id, document_id, version_number, content_hash, storage_path, file_name, extension, size_bytes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (version_id, document_id, version_number, content_hash, str(storage_path), logical_path, Path(logical_path).suffix.lower(), size_bytes),
            )
        return PendingVersion(document_id, version_id, version_number)

    async def activate_version(self, pending: PendingVersion, chunk_count: int) -> str | None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection, connection.transaction():
            previous_result = await connection.execute(
                "SELECT active_version_id FROM knowledge_documents WHERE document_id = %s FOR UPDATE", (pending.document_id,)
            )
            previous = (await previous_result.fetchone())[0]
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'ready', chunk_count = %s, ready_at = NOW() WHERE version_id = %s",
                (chunk_count, pending.version_id),
            )
            if previous and previous != pending.version_id:
                await connection.execute(
                    "UPDATE knowledge_document_versions SET status = 'superseded' WHERE version_id = %s", (previous,)
                )
            await connection.execute(
                "UPDATE knowledge_documents SET active_version_id = %s, status = 'active', updated_at = NOW() WHERE document_id = %s",
                (pending.version_id, pending.document_id),
            )
        previous_id = str(previous) if previous and previous != pending.version_id else None
        self._activate_in_snapshot(pending.version_id, previous_id)
        return previous_id

    async def fail_version(self, version_id: str, error: str) -> None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'failed', error_message = %s WHERE version_id = %s",
                (error[:2000], version_id),
            )

    async def set_storage_path(self, version_id: str, storage_path: Path) -> None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            await connection.execute(
                "UPDATE knowledge_document_versions SET storage_path = %s WHERE version_id = %s",
                (str(storage_path), version_id),
            )

    async def soft_delete(self, logical_path: str) -> list[dict]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection, connection.transaction():
            result = await connection.execute(
                "SELECT document_id, status FROM knowledge_documents WHERE logical_path = %s FOR UPDATE", (logical_path,)
            )
            row = await result.fetchone()
            if not row or row[1] == "deleted":
                raise LookupError("文档不存在或已删除")
            versions_result = await connection.execute(
                "SELECT version_id, storage_path FROM knowledge_document_versions WHERE document_id = %s", (row[0],)
            )
            versions = [{"version_id": str(item[0]), "storage_path": item[1]} for item in await versions_result.fetchall()]
            await connection.execute(
                "UPDATE knowledge_documents SET status = 'deleted', active_version_id = NULL, updated_at = NOW() WHERE document_id = %s", (row[0],)
            )
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'deleted' WHERE document_id = %s", (row[0],)
            )
        self._remove_document_versions_from_snapshot([version["version_id"] for version in versions])
        return versions

    async def get_active_document(self, logical_path: str) -> dict | None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                """SELECT d.document_id, d.logical_path, d.active_version_id, v.file_name, v.extension, v.size_bytes,
                          v.chunk_count, v.version_number, v.content_hash, v.storage_path, v.ready_at
                   FROM knowledge_documents d JOIN knowledge_document_versions v ON v.version_id = d.active_version_id
                   WHERE d.logical_path = %s AND d.status = 'active'""",
                (logical_path,),
            )
            row = await result.fetchone()
        return self._row_to_document(row) if row else None

    async def get_document_by_active_version(self, version_id: str) -> dict | None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                """SELECT d.document_id, d.logical_path, d.active_version_id, v.file_name, v.extension, v.size_bytes,
                          v.chunk_count, v.version_number, v.content_hash, v.storage_path, v.ready_at
                   FROM knowledge_documents d JOIN knowledge_document_versions v ON v.version_id = d.active_version_id
                   WHERE d.active_version_id = %s AND d.status = 'active'""",
                (version_id,),
            )
            row = await result.fetchone()
        return self._row_to_document(row) if row else None

    async def list_active_documents(self) -> list[dict]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                """SELECT d.document_id, d.logical_path, d.active_version_id, v.file_name, v.extension, v.size_bytes,
                          v.chunk_count, v.version_number, v.content_hash, v.storage_path, v.ready_at
                   FROM knowledge_documents d JOIN knowledge_document_versions v ON v.version_id = d.active_version_id
                   WHERE d.status = 'active' ORDER BY d.logical_path"""
            )
            rows = await result.fetchall()
        return [self._row_to_document(row) for row in rows]

    async def versions(self, logical_path: str) -> list[dict]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                """SELECT v.version_id, v.version_number, v.content_hash, v.status, v.chunk_count, v.size_bytes, v.created_at, v.ready_at
                   FROM knowledge_document_versions v JOIN knowledge_documents d ON d.document_id = v.document_id
                   WHERE d.logical_path = %s ORDER BY v.version_number DESC""",
                (logical_path,),
            )
            rows = await result.fetchall()
        return [
            {"version_id": str(row[0]), "version_number": row[1], "content_hash": row[2], "status": row[3], "chunk_count": row[4], "size": row[5], "created_at": row[6].isoformat(), "ready_at": row[7].isoformat() if row[7] else None}
            for row in rows
        ]

    async def rollback(self, logical_path: str, version_id: str) -> tuple[str, str]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection, connection.transaction():
            result = await connection.execute(
                "SELECT document_id, active_version_id FROM knowledge_documents WHERE logical_path = %s FOR UPDATE", (logical_path,)
            )
            document = await result.fetchone()
            if not document:
                raise LookupError("文档不存在")
            candidate_result = await connection.execute(
                "SELECT status FROM knowledge_document_versions WHERE version_id = %s AND document_id = %s", (version_id, document[0])
            )
            candidate = await candidate_result.fetchone()
            if not candidate or candidate[0] not in {"ready", "superseded"}:
                raise ValueError("目标版本不可回滚")
            old_version = str(document[1])
            await connection.execute("UPDATE knowledge_document_versions SET status = 'superseded' WHERE version_id = %s", (old_version,))
            await connection.execute("UPDATE knowledge_document_versions SET status = 'ready' WHERE version_id = %s", (version_id,))
            await connection.execute("UPDATE knowledge_documents SET active_version_id = %s, updated_at = NOW() WHERE document_id = %s", (version_id, document[0]))
        self._activate_in_snapshot(version_id, old_version)
        return old_version, version_id

    @staticmethod
    def _row_to_document(row: tuple) -> dict:
        return {
            "document_id": str(row[0]), "filename": row[3], "source": row[1], "version_id": str(row[2]),
            "extension": str(row[4]).lstrip("."), "size": row[5], "chunk_count": row[6], "version_number": row[7],
            "content_hash": row[8], "storage_path": row[9], "updated_at": row[10].isoformat() if row[10] else None,
            "status": "indexed",
        }


document_version_service = DocumentVersionService()
