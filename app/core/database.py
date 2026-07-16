"""PostgreSQL 连接池与会话记忆表初始化。"""

from loguru import logger
from psycopg_pool import AsyncConnectionPool

from app.config import config

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        title TEXT NOT NULL DEFAULT '新对话',
        agent_type TEXT NOT NULL DEFAULT 'chat',
        summary TEXT NOT NULL DEFAULT '',
        summary_message_count INTEGER NOT NULL DEFAULT 0,
        message_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        sources JSONB NOT NULL DEFAULT '[]'::jsonb,
        tool_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL DEFAULT 'completed',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
    ON chat_sessions (updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created_at
    ON chat_messages (session_id, created_at ASC)
    """,
)


class DatabaseManager:
    """应用级 PostgreSQL 连接池。"""

    def __init__(self) -> None:
        self.pool: AsyncConnectionPool | None = None

    async def connect(self) -> None:
        if self.pool is not None:
            return

        self.pool = AsyncConnectionPool(
            conninfo=config.database_url,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": True},
        )
        await self.pool.open(wait=True)

        async with self.pool.connection() as connection:
            for statement in SCHEMA_STATEMENTS:
                await connection.execute(statement)

        logger.info("PostgreSQL 会话记忆表初始化完成")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def require_pool(self) -> AsyncConnectionPool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL 尚未连接，请先启动应用生命周期")
        return self.pool


database_manager = DatabaseManager()
