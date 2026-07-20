from pathlib import Path

from app.services.bugfix_service import BugFixService
from app.services.bugfix_tools import BugFixRepository


class FakeAuditService:
    def __init__(self):
        self.events = []

    async def record_event(self, _session_id, _request_id, event_type, payload):
        self.events.append((event_type, payload))


class DeterministicBugFixService(BugFixService):
    async def _generate_report(self, state):
        frames = state["parsed_error"]["repository_frames"]
        return f"# Bug 修复建议\n\n定位：`{frames[0]['path']}:{frames[0]['line']}`"


async def test_bugfix_workflow_reads_code_and_returns_location(tmp_path: Path):
    source = tmp_path / "app" / "services" / "orders.py"
    source.parent.mkdir(parents=True)
    source.write_text("def create_order(order):\n    return order.id\n", encoding="utf-8")
    audit = FakeAuditService()
    service = DeterministicBugFixService(BugFixRepository(tmp_path), audit)
    log = '''Traceback (most recent call last):
  File "app/services/orders.py", line 2, in create_order
    return order.id
AttributeError: 'NoneType' object has no attribute 'id'
'''

    events = [event async for event in service.run(log, "bugfix-test")]
    complete = next(event for event in events if event["type"] == "complete")

    assert "app/services/orders.py:2" in complete["response"]
    assert any(event["type"] == "plan" for event in events)
    assert [event_type for event_type, _payload in audit.events] == [
        "bugfix_parse",
        "bugfix_plan",
        "bugfix_execute",
        "bugfix_replan",
        "bugfix_execute",
        "bugfix_replan",
        "bugfix_execute",
        "bugfix_replan",
        "bugfix_execute",
        "bugfix_report",
    ]
