"""RAG Agent 服务 - 基于 LangGraph 的智能代理

使用 langchain_qwq 的 ChatQwen 原生集成，
支持真正的流式输出和更好的模型适配。
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from loguru import logger
from typing_extensions import TypedDict

from app.agent.mcp_client import (
    format_exception_chain,
    get_mcp_client_with_retry,
    load_mcp_tools_safe,
    suggest_mcp_transport,
)
from app.config import config
from app.services.conversation_service import conversation_service
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS, retrieve_knowledge

# 阿里千问大模型和langchain集成参考： https://docs.langchain.com/oss/python/integrations/chat/qwen
# 注意：需要配置环境变量 DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 否则默认访问的是新加坡站点
# 同时也需要配置环境变量 DASHSCOPE_API_KEY=your_api_key


class AgentState(TypedDict):
    """Agent 状态"""

    messages: Annotated[Sequence[BaseMessage], add_messages]


def trim_messages_middleware(state: AgentState) -> dict[str, Any] | None:
    """
    修剪消息历史，只保留最近的几条消息以适应上下文窗口

    策略：
    - 保留第一条系统消息（System Message）
    - 保留最近的 6 条消息（3 轮对话）
    - 当消息少于等于 7 条时，不做修剪

    Args:
        state: Agent 状态

    Returns:
        包含修剪后消息的字典，如果无需修剪则返回 None
    """
    messages = state["messages"]

    # 如果消息数量较少，无需修剪
    if len(messages) <= 7:
        return None

    # 提取第一条系统消息
    first_msg = messages[0]

    # 保留最近的 6 条消息（确保包含完整的对话轮次）
    recent_messages = messages[-6:] if len(messages) % 2 == 0 else messages[-7:]

    # 构建新的消息列表
    new_messages = [first_msg] + list(recent_messages)

    logger.debug(f"修剪消息历史: {len(messages)} -> {len(new_messages)} 条")

    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]}


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatQwen 原生集成"""

    def __init__(self, streaming: bool = True):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
        """
        self.model_name = config.rag_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()

        self.model = ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_key,
            temperature=0.7,
            streaming=streaming,
        )

        # 定义基础工具（与 AIOps Planner/Executor 使用同一套默认本地工具）
        self.tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # 启动阶段会替换为 PostgreSQL Checkpointer；内存实现仅用于导入期兜底。
        self.checkpointer = MemorySaver()

        # Agent 初始化（会在异步方法中完成）
        self.agent = None
        self._agent_initialized = False

        logger.info(
            f"RAG Agent 服务初始化完成 (ChatQwen), model={self.model_name}, streaming={streaming}"
        )

    def configure_checkpointer(self, checkpointer: Any) -> None:
        """在应用启动后注入 PostgreSQL Checkpointer，并重建 Agent。"""
        self.checkpointer = checkpointer
        self.agent = None
        self._agent_initialized = False

    async def _initialize_agent(self):
        """异步初始化 Agent（包括 MCP 工具）"""
        if self._agent_initialized:
            return

        for name, server in config.mcp_servers.items():
            hint = suggest_mcp_transport(
                str(server.get("url", "")),
                str(server.get("transport", "")),
            )
            if hint:
                logger.warning(f"MCP 配置 [{name}]: {hint}")

        mcp_client = await get_mcp_client_with_retry()
        mcp_tools, mcp_err = await load_mcp_tools_safe(mcp_client)
        if mcp_err:
            logger.warning(f"MCP 工具加载失败，将仅使用本地工具继续运行:\n{mcp_err}")
            self.mcp_tools = []
        else:
            self.mcp_tools = mcp_tools
            logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")

        all_tools = self.tools + self.mcp_tools

        self.agent = create_agent(
            self.model,
            tools=all_tools,
            checkpointer=self.checkpointer,
        )

        self._agent_initialized = True

        if all_tools:
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明
            - 当使用知识库检索工具时，引用工具内容中提供的“引用标记”，例如【来源：值班手册.pdf / 第 2 页】
            - 不要捏造来源；没有使用知识库时不要添加来源引用

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def _build_query_messages(
        self,
        question: str,
        session_id: str,
        force_rag: bool,
    ) -> list[BaseMessage]:
        """从 PostgreSQL 组装“摘要 + 最近窗口 + 当前问题”的上下文。"""
        summary, memory_messages = await conversation_service.get_memory_context(session_id)
        messages: list[BaseMessage] = [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            SystemMessage(content=self.system_prompt),
        ]

        if summary:
            messages.append(
                SystemMessage(
                    content=(
                        "以下是较早对话的已确认记忆，仅在与当前问题相关时使用；"
                        "如与最新检索或用户输入冲突，以最新信息为准。\n\n"
                        f"会话摘要：\n{summary}"
                    )
                )
            )

        messages.extend(memory_messages)

        if force_rag:
            context = await retrieve_knowledge.ainvoke({"query": question})
            if not isinstance(context, str):
                context = str(context)
            messages.append(
                SystemMessage(
                    content=(
                        "本次回答已启用强制知识库模式。你必须以如下最新检索内容为依据作答，"
                        "不能使用会话中此前回答替代它，也不要补充检索内容之外的事实。"
                        "如果资料没有答案，请明确说明“知识库中未找到足够信息”。"
                        "每个关键结论后必须保留对应的“引用标记”。\n\n"
                        f"最新知识库检索内容：\n{context}"
                    )
                )
            )

        # API 会先持久化本轮用户消息，因此通常已位于 memory_messages 中；保留兜底。
        if not memory_messages or not isinstance(memory_messages[-1], HumanMessage):
            messages.append(HumanMessage(content=question))
        return messages

    async def query(
        self,
        question: str,
        session_id: str,
        force_rag: bool = False,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Returns:
            str: 完整答案
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            messages = await self._build_query_messages(question, session_id, force_rag)

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {"configurable": {"thread_id": f"rag:{session_id}"}}

            result = await asyncio.wait_for(
                self.agent.ainvoke(
                    input=agent_input,
                    config=config_dict,
                ),
                timeout=config.agent_request_timeout_seconds,
            )

            # 提取最终答案
            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = (
                    last_message.content if hasattr(last_message, "content") else str(last_message)
                )

                # 记录工具调用
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

                logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
                return answer

            logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
            return ""

        except TimeoutError as e:
            logger.error(
                f"[会话 {session_id}] RAG Agent 请求超时: "
                f"{config.agent_request_timeout_seconds} 秒"
            )
            raise TimeoutError("模型响应超时，请稍后重试或新建对话后再试") from e
        except Exception as e:
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（非流式）: {format_exception_chain(e)}"
            )
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
        force_rag: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        流式处理用户问题（逐步返回答案片段）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Yields:
            Dict[str, Any]: 包含流式数据的字典
                - type: "content" | "tool_call" | "complete" | "error"
                - data: 具体内容
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（流式）: {question}")

            messages = await self._build_query_messages(question, session_id, force_rag)

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {"configurable": {"thread_id": f"rag:{session_id}"}}

            async for token, metadata in self.agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = (
                    metadata.get("langgraph_node", "unknown")
                    if isinstance(metadata, dict)
                    else "unknown"
                )
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, "content_blocks", None)

                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_content = block.get("text", "")
                                if text_content:
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name,
                                    }

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            detail = format_exception_chain(e)
            logger.error(f"[会话 {session_id}] RAG Agent 查询失败（流式）: {detail}")
            yield {"type": "error", "data": detail}

    async def clear_checkpoint(self, session_id: str) -> None:
        """删除会话的 Agent Checkpoint；聊天消息由 PostgreSQL 会话服务负责。"""
        thread_id = f"rag:{session_id}"
        delete_async = getattr(self.checkpointer, "adelete_thread", None)
        if delete_async is not None:
            await delete_async(thread_id)
        else:
            self.checkpointer.delete_thread(thread_id)

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=True)
