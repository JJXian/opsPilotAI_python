from types import SimpleNamespace

from app.config import config
from app.services.bm25_retrieval_service import Bm25Document, Bm25SearchResult
from app.services.hybrid_retrieval_service import HybridRetrievalService
from app.services.reranker_service import RerankResult


class FakeVectorSearch:
    def search_similar_documents(self, _query, top_k):
        assert top_k == config.rag_dense_top_k
        return [
            SimpleNamespace(
                id="dense-only",
                content="语义召回结果",
                metadata={"_file_name": "semantic.md"},
            ),
            SimpleNamespace(
                id="shared",
                content="数据库连接池告警处理手册",
                metadata={"_file_name": "database.md"},
            ),
        ]


class FakeBm25Search:
    def search(self, _query, top_k):
        assert top_k == config.rag_bm25_top_k
        return [
            Bm25SearchResult(
                Bm25Document("shared", "数据库连接池告警处理手册", {"_file_name": "database.md"}),
                4.2,
            ),
            Bm25SearchResult(
                Bm25Document("bm25-only", "ORA-12514 错误码排查", {"_file_name": "oracle.md"}),
                3.4,
            ),
        ]


class FakeReranker:
    def rerank(self, _query, documents, top_k):
        assert top_k == config.rag_final_top_k
        shared = next(document for document in documents if document.metadata["_chunk_id"] == "shared")
        return [RerankResult(shared, 0.98)]


def test_hybrid_retrieval_fuses_candidates_and_applies_reranker(monkeypatch):
    monkeypatch.setattr(config, "rag_rerank_enabled", True)
    service = HybridRetrievalService(FakeVectorSearch(), FakeBm25Search(), FakeReranker())

    documents = service.search("数据库连接池告警")

    assert len(documents) == 1
    assert documents[0].metadata["_chunk_id"] == "shared"
    assert documents[0].metadata["_dense_rank"] == 2
    assert documents[0].metadata["_bm25_rank"] == 1
    assert documents[0].metadata["_rerank_score"] == 0.98


def test_hybrid_retrieval_falls_back_to_rrf_when_reranker_fails(monkeypatch):
    class FailingReranker:
        def rerank(self, *_args, **_kwargs):
            raise RuntimeError("rerank unavailable")

    monkeypatch.setattr(config, "rag_rerank_enabled", True)
    service = HybridRetrievalService(FakeVectorSearch(), FakeBm25Search(), FailingReranker())

    documents = service.search("数据库连接池告警")

    assert [document.metadata["_chunk_id"] for document in documents][0] == "shared"
    assert "_rerank_score" not in documents[0].metadata
