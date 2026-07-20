"""显式编排的 Agentic RAG 服务。"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.mcp_client import get_mcp_client_with_retry, load_mcp_tools_safe
from app.config import config
from app.services.agent_audit_service import AgentAuditService, agent_audit_service
from app.services.hybrid_retrieval_service import HybridRetrievalService, hybrid_retrieval_service
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS
from app.tools.knowledge_tool import format_docs


class RoutingDecision(BaseModel):
    intent: Literal["knowledge", "live_data", "general"] = "knowledge"
    should_retrieve: bool = True
    reason: str = Field(description="简短说明为什么需要或不需要知识库")
    search_query: str = Field(description="适合检索的完整查询")


class EvidenceDecision(BaseModel):
    sufficient: bool
    reason: str
    refined_query: str = ""
    clarification_question: str = ""


class AgenticRagState(TypedDict, total=False):
    question: str
    session_id: str
    force_rag: bool
    intent: str
    retrieval_reason: str
    search_query: str
    attempt: int
    documents: list[Document]
    evidence_context: str
    evidence_sufficient: bool
    clarification_question: str
    answer: str
    trace: Annotated[list[dict[str, Any]], operator.add]


class AgenticRagService:
    """将意图、检索、证据评估和回答拆成可观察的 LangGraph 节点。"""

    def __init__(
        self,
        retrieval_service: HybridRetrievalService = hybrid_retrieval_service,
        audit_service: AgentAuditService = agent_audit_service,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.audit_service = audit_service
        self.model = ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_key,
            temperature=0,
        )
        self.checkpointer: Any = MemorySaver()
        self.answer_agent: Any = None
        self._answer_agent_initialized = False
        self.graph = self._build_graph()

    def configure_checkpointer(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.answer_agent = None
        self._answer_agent_initialized = False
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgenticRagState)
        workflow.add_node("classify", self._classify)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("assess", self._assess)
        workflow.add_node("rewrite", self._rewrite)
        workflow.add_node("answer", self._answer)
        workflow.set_entry_point("classify")
        workflow.add_conditional_edges(
            "classify",
            self._after_classify,
            {"retrieve": "retrieve", "answer": "answer"},
        )
        workflow.add_edge("retrieve", "assess")
        workflow.add_conditional_edges(
            "assess",
            self._after_assess,
            {"rewrite": "rewrite", "answer": "answer"},
        )
        workflow.add_edge("rewrite", "retrieve")
        workflow.add_edge("answer", END)
        return workflow.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _trace(stage: str, message: str, **details: Any) -> dict[str, Any]:
        return {"stage": stage, "message": message, "details": details}

    async def _classify(self, state: AgenticRagState) -> dict[str, Any]:
        question = state["question"]
        force_rag = state.get("force_rag", False)
        prompt = (
            "判断用户问题的处理方式。knowledge 表示应检索企业知识库；live_data 表示"
            "主要需要实时工具数据；general 表示寒暄或无需外部事实的通用对话。"
            "不要把不确定的问题归为 general。search_query 必须保留服务名、错误码和关键术语。\n\n"
            f"用户问题：{question}"
        )
        try:
            decision = await self.model.with_structured_output(RoutingDecision).ainvoke(prompt)
        except Exception as error:
            logger.warning("意图识别失败，降级为知识库检索: {}", error)
            decision = RoutingDecision(
                intent="knowledge", should_retrieve=True, reason="意图识别降级，优先查询知识库", search_query=question
            )

        should_retrieve = force_rag or decision.should_retrieve or decision.intent == "knowledge"
        reason = "用户开启知识库优先" if force_rag else decision.reason
        return {
            "intent": decision.intent,
            "retrieval_reason": reason,
            "search_query": decision.search_query.strip() or question,
            "attempt": 0,
            "trace": [
                self._trace(
                    "intent",
                    f"识别为 {decision.intent}，{'开始知识库检索' if should_retrieve else '无需知识库检索'}",
                    intent=decision.intent,
                    reason=reason,
                )
            ],
            "evidence_sufficient": not should_retrieve,
        }

    @staticmethod
    def _after_classify(state: AgenticRagState) -> str:
        return "retrieve" if not state.get("evidence_sufficient", False) else "answer"

    async def _retrieve(self, state: AgenticRagState) -> dict[str, Any]:
        query = state.get("search_query") or state["question"]
        attempt = state.get("attempt", 0) + 1
        try:
            documents = await asyncio.to_thread(self.retrieval_service.search, query)
            context = format_docs(documents) if documents else ""
            sources = [doc.metadata.get("_file_name", "未知来源") for doc in documents]
            message = f"第 {attempt} 次检索完成，召回 {len(documents)} 个片段"
            return {
                "attempt": attempt,
                "documents": documents,
                "evidence_context": context,
                "trace": [
                    self._trace("retrieval", message, query=query, result_count=len(documents), sources=sources)
                ],
            }
        except Exception as error:
            logger.exception("Agentic RAG 检索失败")
            return {
                "attempt": attempt,
                "documents": [],
                "evidence_context": "",
                "trace": [self._trace("retrieval", "知识库检索失败", query=query, error=str(error))],
            }

    async def _assess(self, state: AgenticRagState) -> dict[str, Any]:
        documents = state.get("documents", [])
        attempt = state.get("attempt", 0)
        if not documents:
            exhausted = attempt >= config.agentic_rag_max_retrieval_attempts
            return {
                "evidence_sufficient": False,
                "clarification_question": (
                    "请补充具体的服务名、错误码、文档标题或问题发生的场景。" if exhausted else ""
                ),
                "trace": [
                    self._trace(
                        "evidence",
                        "未找到可用证据，需要补充信息" if exhausted else "未找到可用证据，准备改写查询",
                        sufficient=False,
                    )
                ],
            }

        prompt = (
            "你负责评估检索证据能否回答用户问题。只有当资料直接支持关键结论时才判定充分；"
            "否则给出更精确的 refined_query，必要时给出 clarification_question。\n\n"
            f"用户问题：{state['question']}\n\n检索资料：\n{state.get('evidence_context', '')}"
        )
        try:
            decision = await self.model.with_structured_output(EvidenceDecision).ainvoke(prompt)
        except Exception as error:
            logger.warning("证据评估失败，使用保守降级策略: {}", error)
            decision = EvidenceDecision(sufficient=True, reason="证据评估不可用，保守使用已召回资料")

        can_retry = not decision.sufficient and attempt < config.agentic_rag_max_retrieval_attempts
        clarification = decision.clarification_question if not can_retry else ""
        return {
            "evidence_sufficient": decision.sufficient,
            "clarification_question": clarification,
            "search_query": decision.refined_query.strip() or state.get("search_query", state["question"]),
            "trace": [
                self._trace(
                    "evidence",
                    "证据足以回答" if decision.sufficient else ("证据不足，准备补充检索" if can_retry else "证据不足，需要补充信息"),
                    sufficient=decision.sufficient,
                    reason=decision.reason,
                    attempt=attempt,
                )
            ],
        }

    def _after_assess(self, state: AgenticRagState) -> str:
        if state.get("evidence_sufficient", False):
            return "answer"
        if state.get("attempt", 0) < config.agentic_rag_max_retrieval_attempts:
            return "rewrite"
        return "answer"

    async def _rewrite(self, state: AgenticRagState) -> dict[str, Any]:
        query = state.get("search_query") or state["question"]
        return {
            "trace": [
                self._trace("rewrite", "依据缺失证据改写检索查询", previous_query=query, next_query=query)
            ]
        }

    async def _initialize_answer_agent(self) -> None:
        if self._answer_agent_initialized:
            return
        try:
            mcp_client = await get_mcp_client_with_retry()
            mcp_tools, error = await load_mcp_tools_safe(mcp_client)
            if error:
                logger.warning("MCP 工具加载失败，回答阶段仅使用本地工具: {}", error)
                mcp_tools = []
        except Exception as error:
            logger.warning("MCP 客户端不可用，回答阶段仅使用本地工具: {}", error)
            mcp_tools = []
        self.answer_agent = create_agent(
            self.model,
            tools=[*DEFAULT_LOCAL_AGENT_TOOLS, *mcp_tools],
        )
        self._answer_agent_initialized = True

    async def _answer(self, state: AgenticRagState) -> dict[str, Any]:
        clarification = state.get("clarification_question", "")
        if not state.get("evidence_sufficient", False) and clarification:
            answer = f"目前证据不足以可靠回答。{clarification}"
            return {"answer": answer, "trace": [self._trace("answer", "请求用户补充信息", grounded=False)]}

        await self._initialize_answer_agent()
        evidence = state.get("evidence_context", "")
        system_prompt = """你是可信的知识库问答助手。请严格遵守：
1. 只将“检索证据”中的事实作为知识库结论；每个关键结论后保留对应【来源：...】。
2. 若证据不足，明确说明不知道并提出下一步需要的信息；不要编造来源。
3. 可以按需要调用已注册的实时工具；若调用工具，区分工具实时结果和知识库结论。
4. 输出简洁、结构化的中文答案。
"""
        if evidence:
            system_prompt += f"\n检索证据：\n{evidence}"
        else:
            system_prompt += "\n本轮没有检索到知识库证据。"
        result = await self.answer_agent.ainvoke(
            {"messages": [SystemMessage(content=system_prompt), HumanMessage(content=state["question"])]},
            config={"configurable": {"thread_id": f"agentic-rag:{state['session_id']}"}},
        )
        messages = result.get("messages", [])
        answer = str(getattr(messages[-1], "content", "")) if messages else "未生成回答。"
        return {"answer": answer, "trace": [self._trace("answer", "已基于证据生成回答", grounded=bool(evidence))]}

    async def run(
        self, question: str, session_id: str, force_rag: bool = False
    ) -> AsyncGenerator[dict[str, Any], None]:
        request_id = str(uuid4())
        initial_state: AgenticRagState = {
            "question": question,
            "session_id": session_id,
            "force_rag": force_rag,
            "trace": [],
        }
        config_dict = {"configurable": {"thread_id": f"agentic-rag:{session_id}:{request_id}"}}
        try:
            async for event in self.graph.astream(initial_state, config=config_dict, stream_mode="updates"):
                for _node_name, output in event.items():
                    for trace in output.get("trace", []):
                        await self.audit_service.record_event(session_id, request_id, trace["stage"], trace)
                        yield {"type": "trace", "data": trace}
            final_state = await self.graph.aget_state(config_dict)
            values = final_state.values if final_state and final_state.values else {}
            yield {
                "type": "complete",
                "data": {"answer": values.get("answer", ""), "trace": values.get("trace", [])},
            }
        except Exception as error:
            logger.exception("Agentic RAG 工作流失败")
            await self.audit_service.record_event(session_id, request_id, "error", {"error": str(error)})
            yield {"type": "error", "data": str(error)}

    async def query(self, question: str, session_id: str, force_rag: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"answer": "", "trace": []}
        async for event in self.run(question, session_id, force_rag):
            if event["type"] == "complete":
                result = event["data"]
            elif event["type"] == "error":
                raise RuntimeError(str(event["data"]))
        return result

    async def clear_checkpoint(self, session_id: str) -> None:
        # 每轮请求使用独立 thread_id；聊天正文由 PostgreSQL 会话表管理。
        return None


agentic_rag_service = AgenticRagService()
