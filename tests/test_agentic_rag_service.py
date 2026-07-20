from langchain_core.documents import Document

from app.services.agentic_rag_service import AgenticRagService


class FakeRetrievalService:
    def __init__(self, responses):
        self.responses = responses
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.responses.pop(0)


class FakeAuditService:
    def __init__(self):
        self.events = []

    async def record_event(self, _session_id, _request_id, event_type, payload):
        self.events.append((event_type, payload))


class DeterministicAgenticRagService(AgenticRagService):
    async def _classify(self, state):
        return {
            "intent": "knowledge",
            "retrieval_reason": "测试要求检索",
            "search_query": "initial query",
            "attempt": 0,
            "evidence_sufficient": False,
            "trace": [self._trace("intent", "开始知识库检索")],
        }

    async def _assess(self, state):
        if state["attempt"] == 1:
            return {
                "evidence_sufficient": False,
                "search_query": "refined query",
                "clarification_question": "",
                "trace": [self._trace("evidence", "证据不足，准备补充检索")],
            }
        return {
            "evidence_sufficient": True,
            "trace": [self._trace("evidence", "证据足以回答")],
        }

    async def _answer(self, state):
        return {"answer": "带引用的回答【来源：runbook.md】", "trace": [self._trace("answer", "完成回答")]}


async def test_agentic_rag_rewrites_query_then_answers_from_evidence():
    retrieval = FakeRetrievalService(
        [
            [Document(page_content="不完整资料", metadata={"_file_name": "first.md"})],
            [Document(page_content="完整资料", metadata={"_file_name": "runbook.md"})],
        ]
    )
    audit = FakeAuditService()
    service = DeterministicAgenticRagService(retrieval, audit)

    result = await service.query("如何处理数据库告警", "test-session")

    assert result["answer"] == "带引用的回答【来源：runbook.md】"
    assert retrieval.queries == ["initial query", "refined query"]
    assert [event[0] for event in audit.events] == ["intent", "retrieval", "evidence", "rewrite", "retrieval", "evidence", "answer"]


async def test_no_evidence_after_retry_requests_clarification():
    retrieval = FakeRetrievalService([[], []])
    audit = FakeAuditService()
    service = AgenticRagService(retrieval, audit)

    async def classify(_state):
        return {
            "intent": "knowledge",
            "retrieval_reason": "测试要求检索",
            "search_query": "unknown error",
            "attempt": 0,
            "evidence_sufficient": False,
            "trace": [service._trace("intent", "开始知识库检索")],
        }

    service._classify = classify
    service.graph = service._build_graph()
    result = await service.query("未知错误怎么处理", "test-session")

    assert "目前证据不足" in result["answer"]
    assert "请补充具体的服务名" in result["answer"]
    assert retrieval.queries == ["unknown error", "unknown error"]
