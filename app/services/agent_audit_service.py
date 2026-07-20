"""Agent 执行轨迹的持久化审计服务。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from loguru import logger
from psycopg.types.json import Jsonb

from app.core.database import database_manager


class AgentAuditService:
    """将 Agent 的关键决策保存为独立、可追溯的审计事件。"""

    async def record_event(
        self,
        session_id: str,
        request_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            pool = database_manager.require_pool()
            async with pool.connection() as connection:
                await connection.execute(
                    """
                    INSERT INTO agent_audit_logs (id, session_id, request_id, event_type, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (str(uuid4()), session_id, request_id, event_type, Jsonb(payload)),
                )
        except Exception as error:
            # 审计写入不能阻塞用户问答，但必须留下服务端诊断日志。
            logger.warning("Agent 审计事件写入失败: {}", error)


agent_audit_service = AgentAuditService()
