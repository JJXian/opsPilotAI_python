from types import SimpleNamespace

from langchain_core.documents import Document

from app.config import config
from app.services.reranker_service import RerankerService


def test_reranker_uses_dashscope_response_indexes(monkeypatch):
    response = SimpleNamespace(
        status_code=200,
        output=SimpleNamespace(
            results=[
                SimpleNamespace(index=1, relevance_score=0.91),
                SimpleNamespace(index=0, relevance_score=0.12),
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.reranker_service.TextReRank.call",
        lambda **_kwargs: response,
    )
    monkeypatch.setattr(config, "dashscope_api_key", "test-key")

    results = RerankerService().rerank(
        "连接池告警",
        [Document(page_content="无关内容"), Document(page_content="数据库连接池耗尽处理")],
        top_k=2,
    )

    assert [result.document.page_content for result in results] == [
        "数据库连接池耗尽处理",
        "无关内容",
    ]
    assert results[0].score == 0.91
