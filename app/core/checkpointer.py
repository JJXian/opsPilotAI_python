"""LangGraph PostgreSQL Checkpointer 生命周期管理。"""

from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger

from app.config import config


class CheckpointerManager:
    """让同一个 PostgreSQL Checkpointer 在应用生命周期内复用。"""

    def __init__(self) -> None:
        self._context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self.checkpointer: AsyncPostgresSaver | None = None

    async def connect(self) -> AsyncPostgresSaver:
        if self.checkpointer is not None:
            return self.checkpointer

        self._context = AsyncPostgresSaver.from_conn_string(config.database_url)
        self.checkpointer = await self._context.__aenter__()
        await self.checkpointer.setup()
        logger.info("LangGraph PostgreSQL Checkpointer 初始化完成")
        return self.checkpointer

    async def close(self) -> None:
        if self._context is not None:
            await self._context.__aexit__(None, None, None)
        self._context = None
        self.checkpointer = None


checkpointer_manager = CheckpointerManager()
