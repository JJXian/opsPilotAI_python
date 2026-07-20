"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import aiops, chat, file, health
from app.config import config
from app.core.checkpointer import checkpointer_manager
from app.core.database import database_manager
from app.core.milvus_client import milvus_manager
from app.services.agentic_rag_service import agentic_rag_service
from app.services.aiops_service import aiops_service
from app.services.bm25_retrieval_service import bm25_retrieval_service
from app.services.rag_agent_service import rag_agent_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")

    logger.info("🧠 正在连接 PostgreSQL 记忆库...")
    await database_manager.connect()
    checkpointer = await checkpointer_manager.connect()
    rag_agent_service.configure_checkpointer(checkpointer)
    agentic_rag_service.configure_checkpointer(checkpointer)
    aiops_service.configure_checkpointer(checkpointer)
    logger.info("✅ 会话记忆与 Agent Checkpointer 已持久化到 PostgreSQL")

    # 连接 Milvus
    logger.info("🔌 正在连接 Milvus...")
    milvus_manager.connect()
    logger.info("✅ Milvus 连接成功")

    try:
        bm25_retrieval_service.refresh_from_milvus()
        logger.info("✅ BM25 索引已从 Milvus 重建")
    except Exception as error:
        # BM25 可在首个检索请求时再次懒加载，不能因它阻塞 API 的整体启动。
        logger.warning(f"BM25 启动重建失败，将在首次检索时重试: {error}")

    logger.info("=" * 60)

    yield

    # 关闭时执行
    await checkpointer_manager.close()
    await database_manager.close()
    logger.info("🔌 正在关闭 Milvus 连接...")
    milvus_manager.close()
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的智能oncall运维系统",
    lifespan=lifespan,
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps智能运维"])

# 挂载静态文件
static_dir = "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """返回首页"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host=config.host, port=config.port, reload=config.debug, log_level="info"
    )
