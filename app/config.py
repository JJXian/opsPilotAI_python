"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "DevPilot"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_model: str = "qwen3.7-plus"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # 会话记忆与 LangGraph 状态持久化
    database_url: str = "postgresql://opspilot:opspilot@localhost:5432/opspilot"
    # 同时限制消息数量与字符量，避免单条诊断结果撑爆模型上下文。
    memory_window_messages: int = 10
    memory_summary_trigger_messages: int = 12
    memory_summary_max_chars: int = 2500
    memory_context_max_chars: int = 12000
    memory_message_max_chars: int = 2400
    memory_summary_input_max_chars: int = 12000
    agent_request_timeout_seconds: float = 40.0

    # RAG 配置
    rag_top_k: int = 3  # 兼容旧配置，新的混合检索使用下列分阶段参数
    rag_model: str = "qwen3.7-plus"  # 对话、RAG、Agent 与评测统一使用的主模型
    rag_dense_top_k: int = 10
    rag_bm25_top_k: int = 5
    rag_fusion_top_k: int = 8
    rag_final_top_k: int = 3
    rag_rrf_k: int = 60
    # Reranker 会将候选文本发送给 DashScope；默认关闭，由部署方显式确认后开启。
    rag_rerank_enabled: bool = False
    rag_rerank_model: str = "gte-rerank-v2"
    agentic_rag_max_retrieval_attempts: int = 2

    # Bug 修复 Agent：仅允许读取该目录内的源码与 Git 历史，默认是当前项目根目录。
    bugfix_repository_root: str = "."
    bugfix_max_search_results: int = 12
    bugfix_max_steps: int = 8
    bugfix_token_budget: int = 12000
    bugfix_timeout_seconds: int = 120
    bugfix_tool_retry_attempts: int = 2
    bugfix_tool_timeout_seconds: int = 20
    # 服务器日志源必须由部署方预先配置；接口只接受 source_id，绝不接受客户端传入的 SSH 地址或命令。
    # 例：[{"id":"prod-api","name":"生产 API","host":"10.0.0.8","user":"ops", "log_path":"/var/log/api/error.log"}]
    bugfix_log_sources: list[dict[str, Any]] = []
    bugfix_log_tail_lines: int = 800
    bugfix_log_fetch_timeout_seconds: int = 12

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100
    pdf_ocr_enabled: bool = True
    pdf_ocr_min_text_length: int = 20
    pdf_ocr_render_scale: float = 2.0

    # MCP 服务配置（transport: stdio | sse | streamable-http）
    # 腾讯云托管 MCP 的 URL 通常含 /sse/，需使用 sse；本地 FastMCP 使用 streamable-http
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    # Prometheus
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()
