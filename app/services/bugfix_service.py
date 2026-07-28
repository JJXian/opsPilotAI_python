"""具备动态规划、证据重规划、预算控制和人工审批的 Bugfix Agent。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from loguru import logger
from pydantic import BaseModel, Field

from app.config import config
from app.models.bugfix import BugFixApproval
from app.services.agent_audit_service import AgentAuditService, agent_audit_service
from app.services.bugfix_tools import BugFixRepository, bugfix_repository
from app.tools.query_metrics_alerts import query_prometheus_alerts_api

ToolName = Literal[
    "analyze_logs",
    "inspect_frames",
    "search_code",
    "inspect_git",
    "inspect_releases",
    "inspect_metrics",
    "find_tests",
    "run_tests",
    "verify",
]
RiskLevel = Literal["low", "medium", "high"]
TerminationReason = Literal[
    "completed",
    "evidence_sufficient",
    "max_steps",
    "token_budget",
    "timeout",
    "needs_human_input",
    "human_rejected",
    "no_actionable_plan",
]


class PlannedStep(BaseModel):
    id: str = Field(description="计划内唯一且稳定的步骤 ID")
    tool: ToolName
    objective: str
    query: str = ""


class DiagnosticPlan(BaseModel):
    hypothesis: str
    steps: list[PlannedStep]


class ReplanDecision(BaseModel):
    action: Literal["continue", "replace", "finish", "clarify"]
    reason: str
    steps: list[PlannedStep] = Field(default_factory=list)
    clarification_question: str = ""


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
    incident_id: str
    include_diff: bool
    parsed_error: dict[str, Any]
    hypothesis: str
    plan: list[dict[str, Any]]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    past_steps: Annotated[list[tuple[str, str]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    tool_cache: dict[str, dict[str, Any]]
    approved_action_ids: list[str]
    pending_approval: dict[str, Any] | None
    step_count: int
    estimated_tokens: int
    deadline_at: str
    termination_reason: str
    clarification_question: str
    response: str


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "analyze_logs": {
        "risk": "low",
        "retryable": True,
        "description": "解析当前异常日志、异常类型和关键错误行",
    },
    "inspect_frames": {
        "risk": "low",
        "retryable": True,
        "description": "读取堆栈帧附近的受控仓库代码",
    },
    "search_code": {
        "risk": "low",
        "retryable": True,
        "description": "在受控仓库中搜索异常、函数、配置和调用点",
    },
    "inspect_git": {
        "risk": "low",
        "retryable": True,
        "description": "读取相关文件的 Git 提交历史",
    },
    "inspect_releases": {
        "risk": "low",
        "retryable": True,
        "description": "读取最近发布标签、提交时间和版本变更记录",
    },
    "inspect_metrics": {
        "risk": "medium",
        "retryable": True,
        "description": "只读查询固定 Prometheus 地址的当前告警",
    },
    "find_tests": {
        "risk": "low",
        "retryable": True,
        "description": "发现与异常文件相关的测试",
    },
    "run_tests": {
        "risk": "high",
        "retryable": False,
        "description": "运行自动发现的白名单测试；必须人工审批",
    },
    "verify": {
        "risk": "low",
        "retryable": True,
        "description": "汇总证据、冲突和仍未确认的假设",
    },
}


class BugFixService:
    """通过服务端白名单工具执行动态、可恢复的研发故障诊断。"""

    def __init__(
        self,
        repository: BugFixRepository = bugfix_repository,
        audit_service: AgentAuditService = agent_audit_service,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.model = ChatQwen(
            model=config.rag_model, api_key=config.dashscope_api_key, temperature=0
        )
        self.checkpointer: Any = MemorySaver()
        self.graph = self._build_graph()

    def configure_checkpointer(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(BugFixState)
        workflow.add_node("planner", self._planner)
        workflow.add_node("executor", self._executor)
        workflow.add_node("approval", self._approval)
        workflow.add_node("replanner", self._replanner)
        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "executor")
        workflow.add_conditional_edges(
            "executor",
            self._after_executor,
            {"approval": "approval", "replanner": "replanner"},
        )
        workflow.add_edge("approval", "replanner")
        workflow.add_conditional_edges(
            "replanner",
            lambda state: END if state.get("response") else "executor",
            {"executor": "executor", END: END},
        )
        return workflow.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _trace(stage: str, message: str, **details: Any) -> dict[str, Any]:
        return {"stage": stage, "message": message, "details": details}

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 3)

    @staticmethod
    def _thread_config(incident_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": f"bugfix:{incident_id}"}}

    def _budget_stop_reason(self, state: BugFixState) -> TerminationReason | None:
        if state.get("step_count", 0) >= config.bugfix_max_steps:
            return "max_steps"
        if state.get("estimated_tokens", 0) >= config.bugfix_token_budget:
            return "token_budget"
        deadline = state.get("deadline_at")
        if deadline and datetime.now(UTC) >= datetime.fromisoformat(deadline):
            return "timeout"
        return None

    async def _planner(self, state: BugFixState) -> dict[str, Any]:
        parsed_error = self.repository.parse_python_traceback(state["log"])
        prompt = self._planner_prompt(parsed_error, state["log"])
        try:
            proposal = await self.model.with_structured_output(DiagnosticPlan).ainvoke(prompt)
            if proposal is None:
                raise RuntimeError("Planner 返回空结构")
        except Exception as error:
            logger.warning("动态 Planner 不可用，使用异常类型驱动的保守计划: {}", error)
            proposal = self._fallback_plan(parsed_error)
        plan = self._validate_steps(proposal.steps)
        termination = "" if plan else "no_actionable_plan"
        return {
            "parsed_error": parsed_error,
            "hypothesis": proposal.hypothesis,
            "plan": plan,
            "termination_reason": termination,
            "estimated_tokens": state.get("estimated_tokens", 0) + self._estimate_tokens(prompt),
            "trace": [
                self._trace(
                    "parse",
                    f"已识别 {parsed_error['language']} 异常 {parsed_error['exception_type']}",
                    exception_type=parsed_error["exception_type"],
                    exception_message=parsed_error["exception_message"],
                    repository_frames=parsed_error["repository_frames"],
                ),
                self._trace(
                    "plan",
                    f"Planner 根据异常和可用证据生成 {len(plan)} 步动态计划",
                    hypothesis=proposal.hypothesis,
                    steps=plan,
                ),
            ],
        }

    def _planner_prompt(self, parsed_error: dict[str, Any], log: str) -> str:
        tools = [
            {
                "tool": name,
                "risk": spec["risk"],
                "description": spec["description"],
            }
            for name, spec in TOOL_SPECS.items()
        ]
        return f"""你是生产故障动态诊断 Planner。根据异常语言、异常类型、仓库堆栈帧和现有日志，
从服务端工具清单选择最少且必要的步骤。不同异常必须产生不同计划；不得机械列出全部工具。
优先收集能证伪假设的证据。run_tests 是高风险动作，仅在确实需要验证代码假设时安排。
最多生成 {config.bugfix_max_steps} 步，不得创造工具名。

工具清单：{json.dumps(tools, ensure_ascii=False)}
解析结果：{json.dumps(parsed_error, ensure_ascii=False)}
日志：{log[:6000]}
"""

    def _fallback_plan(self, parsed_error: dict[str, Any]) -> DiagnosticPlan:
        exception = parsed_error["exception_type"].casefold()
        steps = [PlannedStep(id="logs", tool="analyze_logs", objective="确认异常与关键日志")]
        if parsed_error["repository_frames"]:
            steps.append(
                PlannedStep(id="frames", tool="inspect_frames", objective="读取异常位置上下文")
            )
        if any(term in exception for term in ("timeout", "connection", "sql", "io")):
            steps.extend(
                [
                    PlannedStep(
                        id="metrics", tool="inspect_metrics", objective="核对依赖和系统告警"
                    ),
                    PlannedStep(
                        id="releases", tool="inspect_releases", objective="核对近期发布变更"
                    ),
                    PlannedStep(id="code", tool="search_code", objective="搜索连接与超时配置"),
                ]
            )
        elif any(term in exception for term in ("memory", "oom")):
            steps.extend(
                [
                    PlannedStep(id="metrics", tool="inspect_metrics", objective="核对内存相关告警"),
                    PlannedStep(id="code", tool="search_code", objective="搜索内存分配相关代码"),
                ]
            )
        else:
            steps.extend(
                [
                    PlannedStep(id="code", tool="search_code", objective="搜索异常与调用点"),
                    PlannedStep(id="git", tool="inspect_git", objective="检查相关代码近期变化"),
                    PlannedStep(id="tests", tool="find_tests", objective="发现相关回归测试"),
                ]
            )
        steps.append(PlannedStep(id="verify", tool="verify", objective="核验证据与剩余不确定性"))
        return DiagnosticPlan(
            hypothesis=f"{parsed_error['exception_type']} 与异常帧附近逻辑或近期环境变化有关",
            steps=steps,
        )

    def _validate_steps(self, steps: list[PlannedStep]) -> list[dict[str, Any]]:
        validated = []
        seen_ids: set[str] = set()
        for index, step in enumerate(steps[: config.bugfix_max_steps], start=1):
            if step.tool not in TOOL_SPECS:
                continue
            step_id = step.id.strip()[:64] or f"step-{index}"
            if step_id in seen_ids:
                step_id = f"{step_id}-{index}"
            seen_ids.add(step_id)
            validated.append(
                {
                    "id": step_id,
                    "tool": step.tool,
                    "objective": step.objective.strip()[:300],
                    "query": step.query.strip()[:240],
                    # 风险由服务端注册表决定，模型输出无权修改。
                    "risk": TOOL_SPECS[step.tool]["risk"],
                }
            )
        return validated

    async def _executor(self, state: BugFixState) -> dict[str, Any]:
        plan = state.get("plan", [])
        if not plan:
            return {"trace": [self._trace("execute", "没有待执行步骤")]}
        stop_reason = self._budget_stop_reason(state)
        if stop_reason:
            return {
                "plan": [],
                "termination_reason": stop_reason,
                "trace": [self._trace("guard", f"触发执行终止条件：{stop_reason}")],
            }

        step = plan[0]
        if step["risk"] == "high" and step["id"] not in state.get("approved_action_ids", []):
            pending = {
                "step_id": step["id"],
                "tool": step["tool"],
                "objective": step["objective"],
                "risk": step["risk"],
            }
            return {
                "pending_approval": pending,
                "trace": [
                    self._trace(
                        "approval",
                        f"高风险步骤 {step['tool']} 等待人工审批",
                        **pending,
                    )
                ],
            }

        cache = dict(state.get("tool_cache", {}))
        idempotency_key = self._idempotency_key(state["incident_id"], step)
        cached = cache.get(idempotency_key)
        if cached is not None:
            result = cached
            from_cache = True
        else:
            result = await self._execute_with_retry(step, state)
            cache[idempotency_key] = result
            from_cache = False

        evidence = {
            "step_id": step["id"],
            "tool": step["tool"],
            "objective": step["objective"],
            "risk": step["risk"],
            "idempotency_key": idempotency_key,
            "cached": from_cache,
            "result": result,
        }
        result_text = json.dumps(result, ensure_ascii=False, indent=2)
        return {
            "plan": plan[1:],
            "evidence": [evidence],
            "past_steps": [(step["objective"], result_text)],
            "tool_cache": cache,
            "pending_approval": None,
            "step_count": state.get("step_count", 0) + 1,
            "estimated_tokens": state.get("estimated_tokens", 0)
            + self._estimate_tokens(result_text),
            "trace": [
                self._trace(
                    "execute",
                    f"已执行 {step['tool']}：{step['objective']}",
                    step_id=step["id"],
                    tool=step["tool"],
                    risk=step["risk"],
                    idempotency_key=idempotency_key,
                    cached=from_cache,
                    summary=self._result_summary(result),
                )
            ],
        }

    @staticmethod
    def _after_executor(state: BugFixState) -> str:
        return "approval" if state.get("pending_approval") else "replanner"

    async def _approval(self, state: BugFixState) -> dict[str, Any]:
        pending = state["pending_approval"]
        decision = interrupt(
            {
                "type": "approval_required",
                "incident_id": state["incident_id"],
                **pending,
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        reason = str(decision.get("reason", "")) if isinstance(decision, dict) else ""
        if approved:
            approved_ids = [*state.get("approved_action_ids", []), pending["step_id"]]
            return {
                "approved_action_ids": list(dict.fromkeys(approved_ids)),
                "pending_approval": None,
                "trace": [
                    self._trace(
                        "approval",
                        f"人工批准高风险步骤 {pending['tool']}",
                        approved=True,
                        reason=reason,
                    )
                ],
            }
        remaining = [step for step in state.get("plan", []) if step["id"] != pending["step_id"]]
        return {
            "plan": remaining,
            "pending_approval": None,
            "termination_reason": "human_rejected",
            "past_steps": [(pending["objective"], f"人工拒绝执行：{reason or '未说明原因'}")],
            "trace": [
                self._trace(
                    "approval",
                    f"人工拒绝高风险步骤 {pending['tool']}",
                    approved=False,
                    reason=reason,
                )
            ],
        }

    async def _execute_with_retry(self, step: dict[str, Any], state: BugFixState) -> dict[str, Any]:
        spec = TOOL_SPECS[step["tool"]]
        attempts = config.bugfix_tool_retry_attempts if spec["retryable"] else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._execute_tool, step, state),
                    timeout=config.bugfix_tool_timeout_seconds,
                )
                return {"attempt": attempt, "success": True, "data": result}
            except Exception as error:
                last_error = error
                logger.warning(
                    "Bugfix 工具失败: tool={}, attempt={}/{}, error={}",
                    step["tool"],
                    attempt,
                    attempts,
                    error,
                )
        return {
            "attempt": attempts,
            "success": False,
            "error": str(last_error),
        }

    def _execute_tool(self, step: dict[str, Any], state: BugFixState) -> dict[str, Any]:
        tool = step["tool"]
        parsed = state["parsed_error"]
        frames = parsed["repository_frames"]
        paths = [str(frame["path"]) for frame in frames]
        if tool == "analyze_logs":
            important = [
                line.strip()
                for line in state["log"].splitlines()
                if any(
                    token in line.casefold()
                    for token in ("error", "exception", "caused by", "timeout", "failed")
                )
            ]
            return {
                "language": parsed["language"],
                "exception_type": parsed["exception_type"],
                "exception_message": parsed["exception_message"],
                "important_lines": important[-30:],
            }
        if tool == "inspect_frames":
            return {
                "contexts": [
                    self.repository.read_context(frame["path"], int(frame["line"]))
                    for frame in frames
                ]
            }
        if tool == "search_code":
            queries = [
                step.get("query"),
                parsed["exception_type"],
                parsed["exception_message"],
                *[frame["function"] for frame in frames],
            ]
            matches: list[dict[str, Any]] = []
            seen: set[tuple[str, int]] = set()
            for query in (str(item) for item in queries if item):
                for match in self.repository.search_code(query):
                    key = (match["path"], match["line"])
                    if key not in seen:
                        seen.add(key)
                        matches.append(match)
            return {"matches": matches[: config.bugfix_max_search_results]}
        if tool == "inspect_git":
            return {"git_history": self.repository.git_history(paths)}
        if tool == "inspect_releases":
            return {"release_history": self.repository.release_history()}
        if tool == "inspect_metrics":
            body, error = query_prometheus_alerts_api()
            if error:
                return {"available": False, "error": error}
            alerts = ((body.get("data") or {}).get("alerts") or [])[:20]
            return {"available": True, "alerts": alerts}
        if tool == "find_tests":
            return {"related_tests": self.repository.find_related_tests(paths)}
        if tool == "run_tests":
            return self.repository.run_related_tests(paths)
        if tool == "verify":
            return self._verify_evidence(state)
        raise ValueError(f"工具不在服务端白名单中: {tool}")

    def _verify_evidence(self, state: BugFixState) -> dict[str, Any]:
        successful = [
            item["tool"]
            for item in state.get("evidence", [])
            if item.get("result", {}).get("success")
        ]
        failed = [
            item["tool"]
            for item in state.get("evidence", [])
            if not item.get("result", {}).get("success")
        ]
        return {
            "confirmed": [
                f"异常类型：{state['parsed_error']['exception_type']}",
                *[
                    f"仓库堆栈帧：{frame['path']}:{frame['line']}"
                    for frame in state["parsed_error"]["repository_frames"]
                ],
            ],
            "successful_tools": successful,
            "failed_tools": failed,
            "gaps": [
                "日志与代码证据不能替代运行时输入、指标时序和依赖状态；结论仍需复现或测试确认。"
            ],
        }

    async def _replanner(self, state: BugFixState) -> dict[str, Any]:
        stop_reason = self._budget_stop_reason(state)
        if stop_reason:
            response = await self._generate_report({**state, "termination_reason": stop_reason})
            return {
                "plan": [],
                "termination_reason": stop_reason,
                "response": response,
                "trace": [self._trace("report", f"因 {stop_reason} 终止并生成报告")],
            }
        if not state.get("plan"):
            reason = state.get("termination_reason") or "completed"
            response = await self._generate_report({**state, "termination_reason": reason})
            return {
                "termination_reason": reason,
                "response": response,
                "trace": [self._trace("report", "证据收集完成，生成诊断报告")],
            }

        prompt = self._replanner_prompt(state)
        try:
            decision = await self.model.with_structured_output(ReplanDecision).ainvoke(prompt)
            if decision is None:
                raise RuntimeError("Replanner 返回空结构")
        except Exception as error:
            logger.warning("Replanner 不可用，保留剩余计划: {}", error)
            decision = ReplanDecision(action="continue", reason="重规划降级，继续执行已验证计划")

        token_usage = state.get("estimated_tokens", 0) + self._estimate_tokens(prompt)
        if decision.action == "finish":
            response = await self._generate_report(
                {**state, "termination_reason": "evidence_sufficient"}
            )
            return {
                "plan": [],
                "termination_reason": "evidence_sufficient",
                "estimated_tokens": token_usage,
                "response": response,
                "trace": [
                    self._trace(
                        "replan",
                        "Replanner 判定证据充分，删除剩余步骤",
                        reason=decision.reason,
                    )
                ],
            }
        if decision.action == "clarify":
            question = decision.clarification_question or "请补充复现步骤和故障发生时间。"
            response = await self._generate_report(
                {
                    **state,
                    "termination_reason": "needs_human_input",
                    "clarification_question": question,
                }
            )
            return {
                "plan": [],
                "termination_reason": "needs_human_input",
                "clarification_question": question,
                "estimated_tokens": token_usage,
                "response": response,
                "trace": [
                    self._trace(
                        "replan",
                        "Replanner 判定存在必须由用户补充的证据",
                        reason=decision.reason,
                        clarification_question=question,
                    )
                ],
            }
        if decision.action == "replace":
            replacement = self._validate_steps(decision.steps)
            remaining_budget = max(0, config.bugfix_max_steps - state.get("step_count", 0))
            replacement = replacement[:remaining_budget]
            return {
                "plan": replacement,
                "estimated_tokens": token_usage,
                "trace": [
                    self._trace(
                        "replan",
                        f"Replanner 根据证据替换计划，新增/保留 {len(replacement)} 步",
                        reason=decision.reason,
                        steps=replacement,
                    )
                ],
            }
        return {
            "estimated_tokens": token_usage,
            "trace": [
                self._trace(
                    "replan",
                    "Replanner 保留剩余计划",
                    reason=decision.reason,
                    remaining=len(state["plan"]),
                )
            ],
        }

    def _replanner_prompt(self, state: BugFixState) -> str:
        evidence = json.dumps(state.get("evidence", [])[-4:], ensure_ascii=False)[:12000]
        plan = json.dumps(state.get("plan", []), ensure_ascii=False)
        return f"""你是故障诊断 Replanner。判断新证据是否证伪当前假设、是否存在证据缺口。
可以 continue 保留计划、replace 动态增删步骤、finish 提前结束、clarify 请求必要信息。
replace 时只能使用这些工具：{list(TOOL_SPECS)}，总执行步数不得超过 {config.bugfix_max_steps}。
不要为了显得完整而重复已经执行过的工具。

异常：{json.dumps(state["parsed_error"], ensure_ascii=False)}
当前假设：{state.get("hypothesis", "")}
已执行：{state.get("past_steps", [])}
新证据：{evidence}
剩余计划：{plan}
"""

    async def _generate_report(self, state: BugFixState) -> str:
        parsed_error = state["parsed_error"]
        evidence = json.dumps(state.get("evidence", []), ensure_ascii=False, indent=2)[:18000]
        diff_instruction = (
            "在 proposed_diff 中提供统一 diff 草案；只能修改证据定位的文件，必须标注为未应用。"
            if state.get("include_diff", False)
            else "proposed_diff 保持空字符串。"
        )
        prompt = f"""你是资深后端代码审查者。只能根据异常和受控工具证据生成报告。
禁止声称已经应用修改、部署或修复生产故障。所有代码引用必须使用证据中真实存在的 path:line。
终止原因：{state.get("termination_reason", "completed")}。
需要用户补充：{state.get("clarification_question", "") or "无"}。
{diff_instruction}

异常：{state["log"][:8000]}
证据：{evidence}
"""
        try:
            report = await self.model.with_structured_output(BugFixReport).ainvoke(prompt)
            return self._format_report(
                report,
                state.get("include_diff", False),
                state.get("termination_reason", "completed"),
                state.get("clarification_question", ""),
            )
        except Exception as error:
            logger.warning("报告模型不可用，返回确定性证据摘要: {}", error)
            frames = parsed_error["repository_frames"]
            locations = (
                "\n".join(
                    f"- `{frame['path']}:{frame['line']}`（{frame['function']}）"
                    for frame in frames
                )
                or "- 未定位到仓库内堆栈帧"
            )
            return (
                "# Bug 修复建议\n\n## 已确认事实\n"
                f"- 异常：`{parsed_error['exception_type']}`："
                f"{parsed_error['exception_message'] or '无附加消息'}\n{locations}\n\n"
                f"## 终止原因\n- `{state.get('termination_reason', 'completed')}`\n\n"
                "## 不确定项\n- 报告模型不可用，尚不能判断唯一根因。\n\n"
                "## 执行边界\n- 仅使用受控诊断工具；未自动应用代码或执行生产变更。"
            )

    @staticmethod
    def _format_report(
        report: BugFixReport,
        include_diff: bool,
        termination_reason: str,
        clarification_question: str,
    ) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) or "- 无"

        sections = [
            "# Bug 修复建议",
            f"## 疑似根因（置信度：{report.confidence}）\n{report.root_cause}",
            f"## 代码与运行证据\n{bullets(report.evidence)}",
            f"## 修改建议\n{bullets(report.proposed_changes)}",
            f"## 风险\n{bullets(report.risks)}",
            f"## 测试建议\n{bullets(report.test_suggestions)}",
            f"## 不确定项\n{bullets(report.uncertainties)}",
            f"## 终止原因\n- `{termination_reason}`",
        ]
        if clarification_question:
            sections.append(f"## 需要补充\n- {clarification_question}")
        if include_diff:
            diff = report.proposed_diff.strip() or "未能基于当前证据生成安全的 Diff 草案。"
            sections.append(f"## 建议 Diff（未应用）\n```diff\n{diff}\n```")
        sections.append("## 执行边界\n- 工具由服务端白名单和风险等级约束；高风险动作必须人工审批。")
        return "\n\n".join(sections)

    @staticmethod
    def _idempotency_key(incident_id: str, step: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "incident_id": incident_id,
                "tool": step["tool"],
                "query": step.get("query", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> str:
        if not result.get("success"):
            return f"失败：{result.get('error', '未知错误')}"
        data = result.get("data", {})
        for key in (
            "contexts",
            "matches",
            "git_history",
            "release_history",
            "related_tests",
            "alerts",
        ):
            if key in data:
                return f"{key}={len(data[key])}"
        return "已获得受控工具结果"

    async def _stream_execution(
        self,
        graph_input: dict[str, Any] | Command | None,
        *,
        session_id: str,
        incident_id: str,
        request_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        graph_config = self._thread_config(incident_id)
        async for event in self.graph.astream(
            graph_input,
            config=graph_config,
            stream_mode="updates",
        ):
            for node_name, output in event.items():
                if node_name == "__interrupt__":
                    for item in output:
                        yield {"type": "approval_required", "data": item.value}
                    continue
                for trace in output.get("trace", []):
                    await self.audit_service.record_event(
                        session_id,
                        request_id,
                        f"bugfix_{trace['stage']}",
                        {"incident_id": incident_id, **trace},
                    )
                    yield {"type": "trace", "stage": trace["stage"], "data": trace}
                if output.get("plan") is not None and node_name in {"planner", "replanner"}:
                    yield {
                        "type": "plan",
                        "plan": output["plan"],
                        "message": "动态诊断计划已更新",
                    }
                if node_name == "executor" and output.get("past_steps"):
                    step, result = output["past_steps"][-1]
                    yield {"type": "step_complete", "step": step, "result": result}
                if output.get("response"):
                    yield {
                        "type": "report",
                        "report": output["response"],
                        "termination_reason": output.get("termination_reason", ""),
                    }

        snapshot = await self.graph.aget_state(graph_config)
        values = snapshot.values if snapshot and snapshot.values else {}
        if snapshot and snapshot.next:
            yield {
                "type": "paused",
                "incident_id": incident_id,
                "pending_approval": values.get("pending_approval"),
            }
            return
        yield {
            "type": "complete",
            "incident_id": incident_id,
            "response": values.get("response", ""),
            "termination_reason": values.get("termination_reason", ""),
            "step_count": values.get("step_count", 0),
            "estimated_tokens": values.get("estimated_tokens", 0),
            "trace": values.get("trace", []),
        }

    async def run(
        self,
        log: str,
        session_id: str,
        include_diff: bool = False,
        incident_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        stable_incident_id = incident_id or session_id
        request_id = str(uuid4())
        graph_config = self._thread_config(stable_incident_id)
        try:
            existing = await self.graph.aget_state(graph_config)
            if existing and existing.values:
                if existing.next:
                    if "approval" in existing.next:
                        yield {
                            "type": "paused",
                            "incident_id": stable_incident_id,
                            "pending_approval": existing.values.get("pending_approval"),
                            "message": "Incident 已暂停，请提交人工审批结果后继续。",
                        }
                        return
                    async for event in self._stream_execution(
                        None,
                        session_id=session_id,
                        incident_id=stable_incident_id,
                        request_id=request_id,
                    ):
                        yield event
                    return
                if existing.values.get("response"):
                    yield {
                        "type": "complete",
                        "incident_id": stable_incident_id,
                        "response": existing.values["response"],
                        "termination_reason": existing.values.get("termination_reason", ""),
                        "step_count": existing.values.get("step_count", 0),
                        "estimated_tokens": existing.values.get("estimated_tokens", 0),
                        "trace": existing.values.get("trace", []),
                        "cached": True,
                    }
                    return

            initial_state: BugFixState = {
                "log": log,
                "session_id": session_id,
                "incident_id": stable_incident_id,
                "include_diff": include_diff,
                "evidence": [],
                "past_steps": [],
                "trace": [],
                "tool_cache": {},
                "approved_action_ids": [],
                "pending_approval": None,
                "step_count": 0,
                "estimated_tokens": 0,
                "deadline_at": (
                    datetime.now(UTC) + timedelta(seconds=config.bugfix_timeout_seconds)
                ).isoformat(),
                "termination_reason": "",
            }
            async for event in self._stream_execution(
                initial_state,
                session_id=session_id,
                incident_id=stable_incident_id,
                request_id=request_id,
            ):
                yield event
        except Exception as error:
            logger.exception("Bugfix Agent 工作流失败")
            await self.audit_service.record_event(
                session_id,
                request_id,
                "bugfix_error",
                {"incident_id": stable_incident_id, "error": str(error)},
            )
            yield {"type": "error", "incident_id": stable_incident_id, "message": str(error)}

    async def resume(
        self,
        session_id: str,
        incident_id: str,
        approval: BugFixApproval,
    ) -> AsyncGenerator[dict[str, Any], None]:
        request_id = str(uuid4())
        graph_config = self._thread_config(incident_id)
        snapshot = await self.graph.aget_state(graph_config)
        if not snapshot or not snapshot.values or not snapshot.next:
            yield {
                "type": "error",
                "incident_id": incident_id,
                "message": "该 Incident 不存在待恢复的人工审批步骤。",
            }
            return
        try:
            # 人工等待不计入 Agent 执行超时；恢复后重新获得单轮执行时间预算。
            await self.graph.aupdate_state(
                graph_config,
                {
                    "deadline_at": (
                        datetime.now(UTC) + timedelta(seconds=config.bugfix_timeout_seconds)
                    ).isoformat()
                },
            )
            async for event in self._stream_execution(
                Command(resume=approval.model_dump()),
                session_id=session_id,
                incident_id=incident_id,
                request_id=request_id,
            ):
                yield event
        except Exception as error:
            logger.exception("Bugfix Incident 恢复失败")
            yield {"type": "error", "incident_id": incident_id, "message": str(error)}

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        snapshot = await self.graph.aget_state(self._thread_config(incident_id))
        if not snapshot or not snapshot.values:
            return None
        values = snapshot.values
        return {
            "incident_id": incident_id,
            "status": "paused" if snapshot.next else "completed",
            "next_nodes": list(snapshot.next),
            "pending_approval": values.get("pending_approval"),
            "step_count": values.get("step_count", 0),
            "estimated_tokens": values.get("estimated_tokens", 0),
            "termination_reason": values.get("termination_reason", ""),
            "plan": values.get("plan", []),
        }

    async def clear_checkpoint(self, incident_id: str) -> None:
        thread_id = f"bugfix:{incident_id}"
        delete_async = getattr(self.checkpointer, "adelete_thread", None)
        if delete_async is not None:
            await delete_async(thread_id)
            return
        delete_sync = getattr(self.checkpointer, "delete_thread", None)
        if delete_sync is not None:
            delete_sync(thread_id)


bugfix_service = BugFixService()
