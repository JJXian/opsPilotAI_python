"""基于 Plan-Execute-Replan 的只读 Bug 修复建议服务。"""

from __future__ import annotations

import asyncio
import json
import operator
from collections.abc import AsyncGenerator
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.services.agent_audit_service import AgentAuditService, agent_audit_service
from app.services.bugfix_tools import BugFixRepository, bugfix_repository


class BugFixReport(BaseModel):
    root_cause: str
    confidence: str = Field(description="high、medium 或 low")
    evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    proposed_changes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_suggestions: list[str] = Field(default_factory=list)
    proposed_diff: str = ""


class BugFixState(TypedDict, total=False):
    log: str
    session_id: str
    include_diff: bool
    parsed_error: dict[str, Any]
    plan: list[dict[str, str]]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    past_steps: Annotated[list[tuple[str, str]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    response: str


class BugFixService:
    """以固定、只读工具执行研发故障定位，避免模型获得写权限。"""

    def __init__(
        self,
        repository: BugFixRepository = bugfix_repository,
        audit_service: AgentAuditService = agent_audit_service,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.model = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
        self.checkpointer: Any = MemorySaver()
        self.graph = self._build_graph()

    def configure_checkpointer(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(BugFixState)
        workflow.add_node("planner", self._planner)
        workflow.add_node("executor", self._executor)
        workflow.add_node("replanner", self._replanner)
        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "executor")
        workflow.add_edge("executor", "replanner")
        workflow.add_conditional_edges(
            "replanner",
            lambda state: END if state.get("response") else "executor",
            {"executor": "executor", END: END},
        )
        return workflow.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _trace(stage: str, message: str, **details: Any) -> dict[str, Any]:
        return {"stage": stage, "message": message, "details": details}

    async def _planner(self, state: BugFixState) -> dict[str, Any]:
        parsed_error = self.repository.parse_python_traceback(state["log"])
        frames = parsed_error["repository_frames"]
        if not frames:
            return {
                "parsed_error": parsed_error,
                "plan": [],
                "trace": [
                    self._trace(
                        "parse",
                        "已解析异常，但未找到当前项目内的 Python 堆栈帧",
                        exception_type=parsed_error["exception_type"],
                        frame_count=len(parsed_error["frames"]),
                    )
                ],
            }

        plan = [
            {"kind": "inspect_frames", "label": "读取异常帧附近代码"},
            {"kind": "search_code", "label": "检索相关源码与配置"},
            {"kind": "inspect_history", "label": "检查 Git 历史和相关测试"},
            {"kind": "verify", "label": "核验可疑根因的证据"},
        ]
        return {
            "parsed_error": parsed_error,
            "plan": plan,
            "trace": [
                self._trace(
                    "parse",
                    f"已解析 {parsed_error['exception_type']}，定位到 {len(frames)} 个项目内堆栈帧",
                    exception_type=parsed_error["exception_type"],
                    exception_message=parsed_error["exception_message"],
                    frames=frames,
                ),
                self._trace("plan", f"已制定 {len(plan)} 步只读诊断计划", steps=[item["label"] for item in plan]),
            ],
        }

    async def _executor(self, state: BugFixState) -> dict[str, Any]:
        plan = state.get("plan", [])
        if not plan:
            return {"past_steps": [("日志解析", "没有可定位到当前项目的堆栈帧。")]}
        step = plan[0]
        try:
            result = await asyncio.to_thread(self._execute_step, step["kind"], state)
            result_text = json.dumps(result, ensure_ascii=False, indent=2)
            return {
                "plan": plan[1:],
                "evidence": [result],
                "past_steps": [(step["label"], result_text)],
                "trace": [
                    self._trace(
                        "execute",
                        f"已完成：{step['label']}",
                        step=step["kind"],
                        summary=self._result_summary(result),
                    )
                ],
            }
        except Exception as error:
            logger.exception("Bug 修复 Agent 工具步骤失败")
            return {
                "plan": plan[1:],
                "past_steps": [(step["label"], f"只读诊断步骤失败：{error}")],
                "trace": [self._trace("execute", f"步骤失败：{step['label']}", error=str(error))],
            }

    def _execute_step(self, kind: str, state: BugFixState) -> dict[str, Any]:
        parsed_error = state["parsed_error"]
        frames = parsed_error["repository_frames"]
        paths = [str(frame["path"]) for frame in frames]
        if kind == "inspect_frames":
            return {
                "kind": kind,
                "contexts": [self.repository.read_context(frame["path"], int(frame["line"])) for frame in frames],
            }
        if kind == "search_code":
            queries = [parsed_error["exception_type"], parsed_error["exception_message"]]
            queries.extend(frame["function"] for frame in frames)
            matches = []
            seen = set()
            for query in queries:
                for match in self.repository.search_code(str(query)):
                    key = (match["path"], match["line"])
                    if key not in seen:
                        seen.add(key)
                        matches.append(match)
            return {"kind": kind, "matches": matches[: config.bugfix_max_search_results]}
        if kind == "inspect_history":
            return {
                "kind": kind,
                "git_history": self.repository.git_history(paths),
                "related_tests": self.repository.find_related_tests(paths),
            }
        if kind == "verify":
            contexts = next(
                (item.get("contexts", []) for item in state.get("evidence", []) if item.get("kind") == "inspect_frames"),
                [],
            )
            return {
                "kind": kind,
                "confirmed_facts": [
                    f"异常类型：{parsed_error['exception_type']}",
                    *[
                        f"堆栈定位：{item['path']}:{item['line']}"
                        for item in contexts
                        if item.get("found")
                    ],
                ],
                "uncertainties": [
                    "日志无法提供运行时输入、数据库状态或外部依赖响应；根因结论应结合复现或监控数据确认。"
                ],
            }
        raise ValueError(f"未知诊断步骤: {kind}")

    async def _replanner(self, state: BugFixState) -> dict[str, Any]:
        if state.get("plan"):
            return {"trace": [self._trace("replan", "证据尚未收集完成，继续执行下一步", remaining=len(state["plan"]))]}
        response = await self._generate_report(state)
        return {
            "response": response,
            "trace": [self._trace("report", "已生成只读 Bug 修复建议", include_diff=state.get("include_diff", False))],
        }

    async def _generate_report(self, state: BugFixState) -> str:
        parsed_error = state["parsed_error"]
        if not parsed_error["repository_frames"]:
            return (
                "# Bug 修复建议\n\n"
                "## 当前结论\n无法将堆栈中的文件定位到当前项目根目录，因此不应给出具体代码修改建议。\n\n"
                "## 请补充\n- 完整 Python Traceback\n- 与当前仓库一致的代码版本或分支\n- 触发异常的请求参数、配置或复现步骤"
            )

        evidence = json.dumps(state.get("evidence", []), ensure_ascii=False, indent=2)[:18000]
        diff_instruction = (
            "在 proposed_diff 中提供一个统一 diff 草案；只能修改已有证据定位的文件，且必须标注为未应用。"
            if state.get("include_diff", False)
            else "proposed_diff 保持空字符串。"
        )
        prompt = f"""你是资深 Python 代码审查者。基于以下异常堆栈和只读代码证据生成修复建议。
禁止声称已经修改文件、执行命令或验证测试。所有引用必须使用证据中存在的 `path:line`。
堆栈的 `path:line` 仅表示异常发生时的调用位置；只有上下文 snippet 明确支持时才能断言该行的具体逻辑或根因。
若堆栈行与 snippet 不一致，必须写入 uncertainties，不能将错误信息直接当成代码事实。未知信息必须写入 uncertainties。{diff_instruction}

异常堆栈：
{state['log'][:9000]}

代码证据：
{evidence}
"""
        try:
            report = await self.model.with_structured_output(BugFixReport).ainvoke(prompt)
            return self._format_report(report, state.get("include_diff", False))
        except Exception as error:
            logger.warning("Bug 修复报告模型生成失败，返回证据摘要: {}", error)
            frames = parsed_error["repository_frames"]
            locations = "\n".join(f"- `{frame['path']}:{frame['line']}`（{frame['function']}）" for frame in frames)
            return (
                "# Bug 修复建议\n\n## 已确认事实\n"
                f"- 异常：`{parsed_error['exception_type']}`：{parsed_error['exception_message'] or '无附加消息'}\n"
                f"{locations}\n\n## 不确定项\n"
                "- 报告模型不可用，尚未能基于完整上下文判断唯一根因。\n\n"
                "## 建议下一步\n- 根据上述定位行复现异常并补充最小失败用例。"
            )

    @staticmethod
    def _format_report(report: BugFixReport, include_diff: bool) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) or "- 无"

        sections = [
            "# Bug 修复建议",
            f"## 疑似根因（置信度：{report.confidence}）\n{report.root_cause}",
            f"## 代码证据\n{bullets(report.evidence)}",
            f"## 修改建议\n{bullets(report.proposed_changes)}",
            f"## 风险\n{bullets(report.risks)}",
            f"## 测试建议\n{bullets(report.test_suggestions)}",
            f"## 不确定项\n{bullets(report.uncertainties)}",
        ]
        if include_diff:
            diff = report.proposed_diff.strip() or "未能基于当前证据生成安全的 Diff 草案。"
            sections.append(f"## 建议 Diff（未应用）\n```diff\n{diff}\n```")
        sections.append("## 执行边界\n- 本次仅进行了日志、源码和 Git 历史的只读分析，未修改文件、未执行修复命令。")
        return "\n\n".join(sections)

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> str:
        if "contexts" in result:
            return f"读取 {sum(1 for item in result['contexts'] if item.get('found'))} 处代码上下文"
        if "matches" in result:
            return f"找到 {len(result['matches'])} 条相关代码命中"
        if "git_history" in result:
            return f"读取 {len(result['git_history'])} 条 Git 提交与 {len(result['related_tests'])} 个相关测试"
        return "完成证据核验"

    async def run(
        self, log: str, session_id: str, include_diff: bool = False
    ) -> AsyncGenerator[dict[str, Any], None]:
        request_id = str(uuid4())
        config_dict = {"configurable": {"thread_id": f"bugfix:{session_id}:{request_id}"}}
        initial_state: BugFixState = {
            "log": log,
            "session_id": session_id,
            "include_diff": include_diff,
            "evidence": [],
            "past_steps": [],
            "trace": [],
        }
        try:
            async for event in self.graph.astream(initial_state, config=config_dict, stream_mode="updates"):
                for node_name, output in event.items():
                    for trace in output.get("trace", []):
                        await self.audit_service.record_event(session_id, request_id, f"bugfix_{trace['stage']}", trace)
                        yield {"type": "trace", "stage": trace["stage"], "data": trace}
                    if node_name == "planner" and output.get("plan"):
                        yield {"type": "plan", "plan": output["plan"], "message": "Bug 修复诊断计划已生成"}
                    if node_name == "executor" and output.get("past_steps"):
                        step, result = output["past_steps"][-1]
                        yield {"type": "step_complete", "step": step, "result": result}
                    if node_name == "replanner" and output.get("response"):
                        yield {"type": "report", "report": output["response"]}
            final_state = await self.graph.aget_state(config_dict)
            values = final_state.values if final_state and final_state.values else {}
            yield {"type": "complete", "response": values.get("response", ""), "trace": values.get("trace", [])}
        except Exception as error:
            logger.exception("Bug 修复 Agent 工作流失败")
            await self.audit_service.record_event(session_id, request_id, "bugfix_error", {"error": str(error)})
            yield {"type": "error", "message": str(error)}

    async def clear_checkpoint(self, session_id: str) -> None:
        # 每次诊断使用独立 request_id，历史和审计由 PostgreSQL 保存。
        return None


bugfix_service = BugFixService()
