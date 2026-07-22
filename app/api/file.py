"""文件上传接口模块"""

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger

from app.services.bm25_retrieval_service import bm25_retrieval_service
from app.services.document_version_service import document_version_service
from app.services.vector_index_service import vector_index_service
from app.services.vector_store_manager import vector_store_manager

router = APIRouter()

# 文件上传后存储的路径
UPLOAD_DIR = Path("./uploads")
STAGING_DIR = UPLOAD_DIR / "staging"
VERSION_DIR = UPLOAD_DIR / "documents"
# 支持的文件类型
ALLOWED_EXTENSIONS = ["txt", "md", "docx", "pdf", "xlsx"]
# 单个文件支持最大大小
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件并自动创建向量索引

    Args:
        file: 上传的文件

    Returns:
        JSONResponse: 上传结果
    """
    try:
        # 1. 验证文件
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")

        # 2. 规范化文件名（去除空格，处理 Windows 上传的文件）
        safe_filename = _sanitize_filename(file.filename)

        # 3. 验证文件扩展名
        file_extension = _get_file_extension(safe_filename)
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        # 4. 读取内容后以暂存文件 + 不可变版本方式索引，绝不覆盖当前 active 文件。
        content = await file.read()

        # 验证文件大小
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）")

        document = await _store_document_version(safe_filename, content)

        # 6. 返回响应
        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "filename": safe_filename,
                    "file_path": document["storage_path"],
                    "size": len(content),
                    "document_id": document["document_id"],
                    "version_id": document["version_id"],
                    "version_number": document["version_number"],
                },
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {e}") from e


@router.post("/index_directory")
async def index_directory(directory_path: str = None):
    """
    索引指定目录下的所有文件

    Args:
        directory_path: 目录路径（可选，默认使用 uploads 目录）

    Returns:
        JSONResponse: 索引结果
    """
    try:
        logger.info(f"开始索引目录: {directory_path or 'uploads'}")

        # 将旧目录中的平铺文件迁移为版本化文档；不再走会删除旧向量的旧索引流程。
        target = Path(directory_path or UPLOAD_DIR).resolve()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="目录不存在或不是有效目录")
        files = [
            path for path in target.iterdir()
            if path.is_file() and path.suffix.lower().lstrip(".") in ALLOWED_EXTENSIONS
        ]
        imported, failed = [], {}
        for path in files:
            try:
                imported.append(await _store_document_version(path.name, path.read_bytes()))
            except Exception as error:
                failed[path.name] = str(error)

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success" if not failed else "partial_success",
                "data": {"total_files": len(files), "success_count": len(imported), "fail_count": len(failed), "failed_files": failed},
            },
        )

    except Exception as e:
        logger.error(f"索引目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"索引目录失败: {e}") from e


@router.get("/knowledge/stats")
async def get_knowledge_stats():
    """返回已成功建立向量索引的知识库文档统计。"""
    try:
        stats = vector_store_manager.get_knowledge_stats()
        return JSONResponse(
            status_code=200,
            content={"code": 200, "message": "success", "data": stats},
        )
    except Exception as e:
        logger.error(f"获取知识库统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取知识库统计失败: {e}") from e


@router.get("/knowledge/documents")
async def list_knowledge_documents(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=100, description="每页文档数量"),
):
    """返回上传目录与向量库合并后的文档列表及索引状态。"""
    try:
        try:
            documents = await document_version_service.list_active_documents()
        except Exception as error:
            # 首次升级但服务尚未完成建表迁移时，管理页仍可读取历史知识库。
            logger.warning(f"读取版本化文档失败，回退到历史文档视图: {error}")
            documents = []
        documents.extend(_list_legacy_documents({document["filename"] for document in documents}))
        for document in documents:
            document.setdefault("page_count", 0)
            document.setdefault("sheets", [])

        documents.sort(key=lambda document: document["filename"].lower())
        total = len(documents)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        paged_documents = documents[start:start + page_size]

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "documents": paged_documents,
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": total,
                        "total_pages": total_pages,
                    },
                },
            },
        )
    except Exception as e:
        logger.error(f"获取知识库文档列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取知识库文档列表失败: {e}") from e


@router.delete("/knowledge/documents/{filename}")
async def delete_knowledge_document(filename: str):
    """删除本地源文件及其对应的所有向量分片。"""
    try:
        try:
            versions = await document_version_service.soft_delete(filename)
        except LookupError:
            # 历史平铺文档尚未迁移版本表，沿用旧删除逻辑。
            file_path = _get_upload_file_path(filename)
            deleted_chunks = vector_store_manager.delete_by_source(file_path.resolve().as_posix(), ignore_errors=False)
            if not file_path.exists() and deleted_chunks == 0:
                raise HTTPException(status_code=404, detail="文档不存在或已删除") from None
            file_path.unlink(missing_ok=True)
            bm25_retrieval_service.refresh_from_milvus()
            return {"code": 200, "message": "success", "data": {"deleted_chunks": deleted_chunks}}
        deleted_chunks = 0
        for version in versions:
            deleted_chunks += vector_store_manager.delete_by_version(version["version_id"], ignore_errors=False)
            Path(version["storage_path"]).unlink(missing_ok=True)

        bm25_retrieval_service.refresh_from_milvus()

        logger.info(f"知识库文档已删除: {filename}, 分片数={deleted_chunks}")
        return {"code": 200, "message": "success", "data": {"deleted_chunks": deleted_chunks}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除知识库文档失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"删除知识库文档失败: {e}") from e


@router.post("/knowledge/documents/{filename}/reindex")
async def reindex_knowledge_document(filename: str):
    """对已有源文件重新分割、嵌入并替换向量索引。"""
    try:
        document = await _get_or_migrate_document(filename)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在，无法重新索引")
        refreshed = await _store_document_version(filename, Path(document["storage_path"]).read_bytes(), force=True)
        return {"code": 200, "message": "success", "data": refreshed}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重新索引失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"重新索引失败: {e}") from e


@router.post("/knowledge/documents/{filename}/replace")
async def replace_knowledge_document(filename: str, file: UploadFile = File(...)):
    """使用同类型新文件覆盖已有文档，并重建其向量索引。"""
    try:
        document = await _get_or_migrate_document(filename)
        if not document:
            raise HTTPException(status_code=404, detail="原文档不存在，无法覆盖更新")
        if not file.filename:
            raise HTTPException(status_code=400, detail="请选择用于覆盖的文件")

        incoming_extension = _get_file_extension(_sanitize_filename(file.filename))
        current_extension = _get_file_extension(filename)
        if incoming_extension != current_extension:
            raise HTTPException(
                status_code=400,
                detail=f"覆盖更新需保持文件类型一致（当前为 .{current_extension}）",
            )

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）")

        updated = await _store_document_version(filename, content)
        if updated.get("deduplicated") and updated["source"] != filename:
            raise HTTPException(
                status_code=409,
                detail=f"内容与现有文档「{updated['filename']}」完全一致，未创建重复版本",
            )
        logger.info(f"知识库文档覆盖更新成功: {filename}")
        return {"code": 200, "message": "success", "data": updated}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"覆盖更新失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"覆盖更新失败: {e}") from e


@router.get("/knowledge/documents/{filename}/versions")
async def list_document_versions(filename: str):
    """查看不可变历史版本，便于审计和回滚。"""
    try:
        return {"code": 200, "message": "success", "data": {"versions": await document_version_service.versions(filename)}}
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"获取版本历史失败: {error}") from error


@router.post("/knowledge/documents/{filename}/rollback/{version_id}")
async def rollback_document_version(filename: str, version_id: str):
    """原子切换到已完成索引的历史版本；不重新解析或向量化。"""
    try:
        previous, active = await document_version_service.rollback(filename, version_id)
        bm25_retrieval_service.refresh_from_milvus()
        return {"code": 200, "message": "success", "data": {"previous_version_id": previous, "active_version_id": active}}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"回滚失败: {error}") from error


async def _store_document_version(filename: str, content: bytes, *, force: bool = False) -> dict:
    """暂存、构建、校验、激活一个新版本；失败时当前 active version 不变。"""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(content).hexdigest()
    staging_path = STAGING_DIR / f"{content_hash}-{uuid4().hex}-{filename}"
    staging_path.write_bytes(content)
    pending = await document_version_service.create_pending_version(
        filename, content_hash, staging_path, len(content), force=force
    )
    if pending.duplicate:
        staging_path.unlink(missing_ok=True)
        existing = await document_version_service.get_document_by_active_version(pending.version_id)
        if not existing:
            raise RuntimeError("重复内容的 active 文档不存在")
        existing["deduplicated"] = True
        return existing

    version_path = VERSION_DIR / pending.document_id / pending.version_id / filename
    activated = False
    try:
        version_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.replace(version_path)
        await document_version_service.set_storage_path(pending.version_id, version_path)
        chunk_count = vector_index_service.index_version(
            str(version_path), document_id=pending.document_id, version_id=pending.version_id, logical_path=filename
        )
        await document_version_service.activate_version(pending, chunk_count)
        activated = True
        try:
            bm25_retrieval_service.refresh_from_milvus()
        except Exception as error:
            # active version 已切换，BM25 的查询时过滤仍可阻止旧版本泄漏；后续刷新可自愈。
            logger.warning(f"版本已激活，但 BM25 刷新失败，将在下次检索时重试: {error}")
        document = await document_version_service.get_active_document(filename)
        if not document:
            raise RuntimeError("激活版本后未找到 active 文档")
        return document
    except Exception as error:
        if not activated:
            vector_store_manager.delete_by_version(pending.version_id)
            await document_version_service.fail_version(pending.version_id, str(error))
            staging_path.unlink(missing_ok=True)
            version_path.unlink(missing_ok=True)
        raise


async def _get_or_migrate_document(filename: str) -> dict | None:
    """操作旧平铺文件时按需迁入版本模型，避免管理页升级后按钮失效。"""
    try:
        document = await document_version_service.get_active_document(filename)
    except Exception:
        document = None
    if document:
        return document
    file_path = _get_upload_file_path(filename)
    if not file_path.exists():
        return None
    legacy_source = file_path.resolve().as_posix()
    document = await _store_document_version(filename, file_path.read_bytes(), force=True)
    vector_store_manager.delete_by_source(legacy_source)
    file_path.unlink(missing_ok=True)
    bm25_retrieval_service.refresh_from_milvus()
    return document


def _list_legacy_documents(versioned_filenames: set[str]) -> list[dict]:
    """升级期保留旧 `_source` 管理视图，避免已有知识库在新表为空时消失。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    indexed_documents = {
        document["source"]: document
        for document in vector_store_manager.list_indexed_documents()
    }
    documents: list[dict] = []
    for file_path in UPLOAD_DIR.iterdir():
        if not file_path.is_file() or _get_file_extension(file_path.name) not in ALLOWED_EXTENSIONS:
            continue
        if file_path.name in versioned_filenames:
            continue
        resolved_path = file_path.resolve().as_posix()
        indexed = indexed_documents.pop(resolved_path, None)
        stat = file_path.stat()
        documents.append(
            {
                "filename": file_path.name,
                "source": resolved_path,
                "extension": _get_file_extension(file_path.name),
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                "status": "indexed" if indexed else "unindexed",
                "chunk_count": indexed["chunk_count"] if indexed else 0,
                "page_count": indexed["page_count"] if indexed else 0,
                "sheets": indexed["sheets"] if indexed else [],
                "legacy": True,
            }
        )
    return documents


def _get_file_extension(filename: str) -> str:
    """
    获取文件扩展名

    Args:
        filename: 文件名

    Returns:
        str: 扩展名（小写，不含点）
    """
    parts = filename.rsplit(".", 1)
    if len(parts) == 2:
        return parts[1].lower()
    return ""


def _sanitize_filename(filename: str) -> str:
    """
    规范化文件名，去除空格和特殊字符

    Args:
        filename: 原始文件名

    Returns:
        str: 规范化后的文件名
    """
    # 去除空格
    sanitized = filename.replace(" ", "_")
    # 去除其他可能导致问题的字符
    for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        sanitized = sanitized.replace(char, "_")
    return sanitized


def _get_upload_file_path(filename: str) -> Path:
    """将 URL 中的文件名安全地映射为 uploads 下的文件路径。"""
    safe_filename = _sanitize_filename(filename)
    if safe_filename != filename or not safe_filename:
        raise HTTPException(status_code=400, detail="无效的文档名称")

    upload_dir = UPLOAD_DIR.resolve()
    file_path = (upload_dir / safe_filename).resolve()
    if file_path.parent != upload_dir:
        raise HTTPException(status_code=400, detail="无效的文档路径")
    return file_path
