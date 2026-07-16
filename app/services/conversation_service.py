"""会话、消息与摘要记忆的持久化服务。"""

import re
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from loguru import logger
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import config
from app.core.database import database_manager

SOURCE_PATTERN = re.compile(r"【来源：([^】]+)】")


class ConversationService:
    """以 PostgreSQL 为唯一事实来源管理聊天历史。"""

    @staticmethod
    def _compact_memory_content(content: str, max_chars: int) -> str:
        """保留长消息首尾关键信息，防止诊断结果无限放大上下文。"""
        if len(content) <= max_chars:
            return content

        head_chars = max_chars * 2 // 3
        tail_chars = max_chars - head_chars
        omitted = len(content) - max_chars
        return (
            f"{content[:head_chars]}\n\n"
            f"[中间省略 {omitted} 个字符的历史诊断内容]\n\n"
            f"{content[-tail_chars:]}"
        )

    async def ensure_session(self, session_id: str, agent_type: str = "chat") -> None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO chat_sessions (id, agent_type)
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE
                SET updated_at = NOW(), agent_type = EXCLUDED.agent_type
                """,
                (session_id, agent_type),
            )

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        status: str = "completed",
        sources: list[dict[str, Any]] | None = None,
        tool_payload: dict[str, Any] | None = None,
        agent_type: str = "chat",
    ) -> str:
        await self.ensure_session(session_id, agent_type)
        message_id = str(uuid4())
        sources = sources if sources is not None else self._extract_sources(content)
        pool = database_manager.require_pool()

        async with pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, content, sources, tool_payload, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message_id,
                    session_id,
                    role,
                    content,
                    Jsonb(sources),
                    Jsonb(tool_payload or {}),
                    status,
                ),
            )
            await connection.execute(
                """
                UPDATE chat_sessions
                SET message_count = message_count + 1, updated_at = NOW(),
                    title = CASE
                        WHEN title = '新对话' AND %s = 'user' THEN LEFT(%s, 32)
                        ELSE title
                    END
                WHERE id = %s
                """,
                (role, self._make_title(content), session_id),
            )
        return message_id

    async def list_sessions(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT id, title, agent_type, message_count, created_at, updated_at
                    FROM chat_sessions
                    WHERE status = 'active'
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size, (page - 1) * page_size),
                )
                sessions = await cursor.fetchall()
                await cursor.execute(
                    "SELECT COUNT(*) AS total FROM chat_sessions WHERE status = 'active'"
                )
                total_row = await cursor.fetchone()
        return {
            "items": sessions,
            "page": page,
            "page_size": page_size,
            "total": total_row["total"],
        }

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT id, title, agent_type, summary, summary_message_count,
                           message_count, created_at, updated_at
                    FROM chat_sessions WHERE id = %s AND status = 'active'
                    """,
                    (session_id,),
                )
                return await cursor.fetchone()

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT id, role, content, sources, tool_payload, status, created_at
                    FROM chat_messages WHERE session_id = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                return await cursor.fetchall()

    async def get_memory_context(self, session_id: str) -> tuple[str, list[BaseMessage]]:
        session = await self.get_session(session_id)
        if session is None:
            return "", []

        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT role, content FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (session_id, config.memory_window_messages),
                )
                rows = list(reversed(await cursor.fetchall()))

        # 从最新消息向前装载，消息条数和总字符数任一触顶即停止。
        # 这样一段很长的工具诊断结果不会挤掉当前问题，也不会拖慢模型调用。
        selected_rows: list[tuple[str, str]] = []
        used_chars = 0
        for row in reversed(rows):
            content = self._compact_memory_content(
                row["content"], config.memory_message_max_chars
            )
            if selected_rows and used_chars + len(content) > config.memory_context_max_chars:
                continue
            selected_rows.append((row["role"], content))
            used_chars += len(content)

        messages: list[BaseMessage] = []
        for role, content in reversed(selected_rows):
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return session["summary"], messages

    async def maybe_refresh_summary(self, session_id: str, summarizer: Any) -> None:
        session = await self.get_session(session_id)
        if session is None:
            return

        message_count = session["message_count"]
        summarized_count = session["summary_message_count"]
        keep_count = config.memory_window_messages
        if (
            message_count < config.memory_summary_trigger_messages
            or message_count - summarized_count <= keep_count
        ):
            return

        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT role, content FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    OFFSET %s LIMIT %s
                    """,
                    (session_id, summarized_count, message_count - summarized_count - keep_count),
                )
                rows = await cursor.fetchall()

        if not rows:
            return

        transcript_parts: list[str] = []
        transcript_chars = 0
        for row in reversed(rows):
            content = self._compact_memory_content(
                row["content"], config.memory_message_max_chars
            )
            item = f"{row['role']}: {content}"
            if transcript_parts and transcript_chars + len(item) > config.memory_summary_input_max_chars:
                break
            transcript_parts.append(item)
            transcript_chars += len(item)
        transcript = "\n".join(reversed(transcript_parts))
        prompt = (
            "请增量维护一份简洁的运维会话记忆。保留服务名、环境、告警、已确认事实、"
            "工具查询结果、用户约束、未解决问题和下一步；不要编造事实。\n\n"
            f"已有摘要：\n{session['summary'] or '（无）'}\n\n"
            f"新增历史：\n{transcript}\n\n"
            "只输出更新后的摘要。"
        )
        result = await summarizer.ainvoke(prompt)
        summary = str(getattr(result, "content", result)).strip()[: config.memory_summary_max_chars]
        if not summary:
            return

        async with pool.connection() as connection:
            await connection.execute(
                """
                UPDATE chat_sessions
                SET summary = %s, summary_message_count = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (summary, message_count - keep_count, session_id),
            )
        logger.info(f"[会话 {session_id}] 已更新摘要记忆，覆盖 {message_count - keep_count} 条消息")

    async def clear_session(self, session_id: str) -> bool:
        pool = database_manager.require_pool()
        async with pool.connection() as connection:
            result = await connection.execute(
                "DELETE FROM chat_sessions WHERE id = %s", (session_id,)
            )
        return result.rowcount > 0

    @staticmethod
    def _make_title(content: str) -> str:
        normalized = " ".join(content.split())
        return normalized[:32] or "新对话"

    @staticmethod
    def _extract_sources(content: str) -> list[dict[str, str]]:
        return [
            {"label": label.strip()} for label in dict.fromkeys(SOURCE_PATTERN.findall(content))
        ]


conversation_service = ConversationService()
