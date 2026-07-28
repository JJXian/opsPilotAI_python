from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import config
from app.models.bugfix import BugFixApproval, BugFixRequest
from app.services.bugfix_service import (
    BugFixService,
    PlannedStep,
    ReplanDecision,
)
from app.services.bugfix_tools import BugFixRepository


class FakeAuditService:
    def __init__(self):
        self.events = []

    async def record_event(self, _session_id, _request_id, event_type, payload):
        self.events.append((event_type, payload))


class DeterministicBugFixService(BugFixService):
    """保留生产执行器，只将外部 LLM 决策替换为确定性计划。"""

    async def _planner(self, state):
        parsed = self.repository.parse_python_traceback(state["log"])
        proposal = self._fallback_plan(parsed)
        plan = self._validate_steps(proposal.steps)
        return {
            "parsed_error": parsed,
            "hypothesis": proposal.hypothesis,
            "plan": plan,
            "termination_reason": "",
            "estimated_tokens": 100,
            "trace": [
                self._trace("parse", f"已识别 {parsed['exception_type']}"),
                self._trace("plan", "已生成异常类型驱动的动态计划", steps=plan),
            ],
        }

    async def _replanner(self, state):
        if state.get("plan"):
            return {"trace": [self._trace("replan", "继续执行剩余计划")]}
        reason = state.get("termination_reason") or "completed"
        response = await self._generate_report({**state, "termination_reason": reason})
        return {
            "termination_reason": reason,
            "response": response,
            "trace": [self._trace("report", "生成报告")],
        }

    async def _generate_report(self, state):
        frames = state["parsed_error"]["repository_frames"]
        location = f"{frames[0]['path']}:{frames[0]['line']}" if frames else "无仓库帧"
        return (
            f"# Bug 修复建议\n\n定位：`{location}`\n\n"
            f"终止原因：`{state.get('termination_reason', 'completed')}`"
        )


class HighRiskBugFixService(DeterministicBugFixService):
    async def _planner(self, state):
        parsed = self.repository.parse_python_traceback(state["log"])
        plan = self._validate_steps(
            [
                PlannedStep(
                    id="run-tests",
                    tool="run_tests",
                    objective="运行自动发现的相关测试验证假设",
                )
            ]
        )
        return {
            "parsed_error": parsed,
            "hypothesis": "需要测试验证",
            "plan": plan,
            "termination_reason": "",
            "estimated_tokens": 50,
            "trace": [
                self._trace("parse", f"已识别 {parsed['exception_type']}"),
                self._trace("plan", "生成高风险验证步骤", steps=plan),
            ],
        }


class CountingRepository(BugFixRepository):
    def __init__(self, root):
        super().__init__(root)
        self.test_runs = 0

    def run_related_tests(self, paths):
        self.test_runs += 1
        return {"executed": True, "return_code": 0, "tests": ["tests/test_orders.py"]}


def make_log() -> str:
    return """Traceback (most recent call last):
  File "app/services/orders.py", line 2, in create_order
    return order.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def prepare_repository(tmp_path: Path, repository_class=BugFixRepository):
    source = tmp_path / "app" / "services" / "orders.py"
    source.parent.mkdir(parents=True)
    source.write_text("def create_order(order):\n    return order.id\n", encoding="utf-8")
    return repository_class(tmp_path)


async def test_dynamic_bugfix_workflow_reads_code_and_returns_location(tmp_path: Path):
    audit = FakeAuditService()
    service = DeterministicBugFixService(prepare_repository(tmp_path), audit)

    events = [
        event
        async for event in service.run(
            make_log(),
            "bugfix-session",
            incident_id="incident-dynamic-001",
        )
    ]
    complete = next(event for event in events if event["type"] == "complete")
    initial_plan = next(event for event in events if event["type"] == "plan")["plan"]

    assert "app/services/orders.py:2" in complete["response"]
    assert complete["termination_reason"] == "completed"
    assert complete["step_count"] == len(initial_plan)
    assert [step["tool"] for step in initial_plan] == [
        "analyze_logs",
        "inspect_frames",
        "search_code",
        "inspect_git",
        "find_tests",
        "verify",
    ]
    assert all("id" in step and "risk" in step for step in initial_plan)
    assert any(event_type == "bugfix_replan" for event_type, _payload in audit.events)


def test_fallback_planner_changes_plan_for_connection_failure(tmp_path: Path):
    service = DeterministicBugFixService(prepare_repository(tmp_path), FakeAuditService())
    attribute_plan = service._fallback_plan(
        {"exception_type": "AttributeError", "repository_frames": [{"path": "a.py"}]}
    )
    connection_plan = service._fallback_plan(
        {"exception_type": "ConnectionError", "repository_frames": [{"path": "a.py"}]}
    )

    assert [step.tool for step in attribute_plan.steps] != [
        step.tool for step in connection_plan.steps
    ]
    assert "inspect_metrics" in [step.tool for step in connection_plan.steps]
    assert "inspect_releases" in [step.tool for step in connection_plan.steps]


async def test_high_risk_step_pauses_and_resumes_same_incident(tmp_path: Path):
    repository = CountingRepository(tmp_path)
    prepare_repository(tmp_path)
    audit = FakeAuditService()
    service = HighRiskBugFixService(repository, audit)

    first_events = [
        event
        async for event in service.run(
            make_log(),
            "approval-session",
            incident_id="incident-approval-001",
        )
    ]

    assert any(event["type"] == "approval_required" for event in first_events)
    assert first_events[-1]["type"] == "paused"
    status = await service.get_incident("incident-approval-001")
    assert status["status"] == "paused"
    assert repository.test_runs == 0

    resumed_events = [
        event
        async for event in service.resume(
            "approval-session",
            "incident-approval-001",
            BugFixApproval(approved=True, reason="允许运行白名单测试"),
        )
    ]

    assert resumed_events[-1]["type"] == "complete"
    assert resumed_events[-1]["termination_reason"] == "completed"
    assert repository.test_runs == 1

    repeated_events = [
        event
        async for event in service.run(
            make_log(),
            "approval-session",
            incident_id="incident-approval-001",
        )
    ]
    assert repeated_events == [
        {
            **resumed_events[-1],
            "cached": True,
        }
    ]
    assert repository.test_runs == 1


async def test_rejected_high_risk_step_is_not_executed(tmp_path: Path):
    repository = CountingRepository(tmp_path)
    prepare_repository(tmp_path)
    service = HighRiskBugFixService(repository, FakeAuditService())
    _ = [
        event
        async for event in service.run(
            make_log(),
            "reject-session",
            incident_id="incident-reject-001",
        )
    ]

    resumed = [
        event
        async for event in service.resume(
            "reject-session",
            "incident-reject-001",
            BugFixApproval(approved=False, reason="当前环境禁止执行测试"),
        )
    ]

    assert resumed[-1]["termination_reason"] == "human_rejected"
    assert repository.test_runs == 0


async def test_tool_idempotency_cache_avoids_duplicate_high_risk_execution(tmp_path: Path):
    repository = CountingRepository(tmp_path)
    prepare_repository(tmp_path)
    service = HighRiskBugFixService(repository, FakeAuditService())
    parsed = repository.parse_python_traceback(make_log())
    step = {
        "id": "run-tests",
        "tool": "run_tests",
        "objective": "运行测试",
        "query": "",
        "risk": "high",
    }
    state = {
        "incident_id": "incident-cache-001",
        "log": make_log(),
        "parsed_error": parsed,
        "plan": [step],
        "approved_action_ids": ["run-tests"],
        "tool_cache": {},
        "step_count": 0,
        "estimated_tokens": 0,
        "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }

    first = await service._executor(state)
    second = await service._executor(
        {
            **state,
            "tool_cache": first["tool_cache"],
        }
    )

    assert repository.test_runs == 1
    assert first["evidence"][0]["cached"] is False
    assert second["evidence"][0]["cached"] is True
    assert first["evidence"][0]["idempotency_key"] == second["evidence"][0]["idempotency_key"]


async def test_replanner_can_replace_remaining_steps_from_evidence(tmp_path: Path):
    service = BugFixService(prepare_repository(tmp_path), FakeAuditService())

    class FakeStructuredModel:
        async def ainvoke(self, _prompt):
            return ReplanDecision(
                action="replace",
                reason="日志显示连接异常，删除 Git 步骤并补充指标与发布证据",
                steps=[
                    PlannedStep(id="metrics", tool="inspect_metrics", objective="检查告警"),
                    PlannedStep(
                        id="releases",
                        tool="inspect_releases",
                        objective="检查近期发布",
                    ),
                ],
            )

    class FakeModel:
        def with_structured_output(self, _schema):
            return FakeStructuredModel()

    service.model = FakeModel()
    state = {
        "parsed_error": {
            "language": "python",
            "exception_type": "ConnectionError",
            "exception_message": "refused",
            "repository_frames": [],
        },
        "hypothesis": "数据库连接失败",
        "evidence": [
            {
                "tool": "analyze_logs",
                "result": {"success": True, "data": {"exception": "ConnectionError"}},
            }
        ],
        "past_steps": [("解析日志", "ConnectionError")],
        "plan": [
            {
                "id": "git",
                "tool": "inspect_git",
                "objective": "检查 Git",
                "query": "",
                "risk": "low",
            }
        ],
        "step_count": 1,
        "estimated_tokens": 100,
        "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }

    result = await service._replanner(state)

    assert [step["tool"] for step in result["plan"]] == [
        "inspect_metrics",
        "inspect_releases",
    ]
    assert "替换计划" in result["trace"][0]["message"]


async def test_token_budget_stops_agent_before_tool_execution(tmp_path: Path, monkeypatch):
    repository = CountingRepository(tmp_path)
    prepare_repository(tmp_path)
    service = DeterministicBugFixService(repository, FakeAuditService())
    monkeypatch.setattr(config, "bugfix_token_budget", 50)

    events = [
        event
        async for event in service.run(
            make_log(),
            "budget-session",
            incident_id="incident-budget-001",
        )
    ]

    assert events[-1]["termination_reason"] == "token_budget"
    assert events[-1]["step_count"] == 0


def test_bugfix_request_requires_incident_for_approval():
    request = BugFixRequest(
        session_id="session",
        incident_id="incident-model-001",
        approval=BugFixApproval(approved=True),
    )
    assert request.log is None

    with pytest.raises(ValidationError):
        BugFixRequest(
            session_id="session",
            approval=BugFixApproval(approved=True),
        )
