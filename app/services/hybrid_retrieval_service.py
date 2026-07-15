"""混合检索服务 - Dense Retrieval + BM25 + RRF + Reranker。"""

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.services.bm25_retrieval_service import Bm25RetrievalService, bm25_retrieval_service
from app.services.reranker_service import RerankerService, reranker_service
from app.services.vector_search_service import VectorSearchService, vector_search_service


@dataclass
class HybridCandidate:
    id: str
    content: str
    metadata: dict[str, Any]
    rrf_score: float = 0.0
    dense_rank: int | None = None
    bm25_rank: int | None = None

    def to_document(self) -> Document:
        metadata = dict(self.metadata)
        metadata.update(
            {
                "_chunk_id": self.id,
                "_retrieval": "hybrid",
                "_rrf_score": round(self.rrf_score, 6),
            }
        )
        if self.dense_rank is not None:
            metadata["_dense_rank"] = self.dense_rank
        if self.bm25_rank is not None:
            metadata["_bm25_rank"] = self.bm25_rank
        return Document(page_content=self.content, metadata=metadata)


class HybridRetrievalService:
    """以 RRF 融合两路召回，并在可用时用 Reranker 精排。"""

    def __init__(
        self,
        vector_search: VectorSearchService = vector_search_service,
        bm25_search: Bm25RetrievalService = bm25_retrieval_service,
        reranker: RerankerService = reranker_service,
    ) -> None:
        self.vector_search = vector_search
        self.bm25_search = bm25_search
        self.reranker = reranker

    def search(self, query: str) -> list[Document]:
        candidates: dict[str, HybridCandidate] = {}
        dense_count = 0
        bm25_count = 0

        try:
            dense_results = self.vector_search.search_similar_documents(
                query,
                top_k=config.rag_dense_top_k,
            )
            dense_count = len(dense_results)
            for rank, result in enumerate(dense_results, start=1):
                candidate = candidates.setdefault(
                    result.id,
                    HybridCandidate(result.id, result.content, dict(result.metadata)),
                )
                candidate.dense_rank = rank
                candidate.rrf_score += self._rrf_score(rank)
        except Exception as error:
            logger.warning(f"Dense Retrieval 失败，将继续使用 BM25: {error}")

        try:
            bm25_results = self.bm25_search.search(query, top_k=config.rag_bm25_top_k)
            bm25_count = len(bm25_results)
            for rank, result in enumerate(bm25_results, start=1):
                document = result.document
                candidate = candidates.setdefault(
                    document.id,
                    HybridCandidate(document.id, document.content, dict(document.metadata)),
                )
                candidate.bm25_rank = rank
                candidate.rrf_score += self._rrf_score(rank)
        except Exception as error:
            logger.warning(f"BM25 Retrieval 失败，将继续使用 Dense Retrieval: {error}")

        fused_candidates = sorted(
            candidates.values(),
            key=lambda candidate: candidate.rrf_score,
            reverse=True,
        )[: config.rag_fusion_top_k]
        fused_documents = [candidate.to_document() for candidate in fused_candidates]

        if not fused_documents:
            logger.warning(f"混合检索未找到候选: query='{query}'")
            return []

        if config.rag_rerank_enabled:
            try:
                reranked = self.reranker.rerank(
                    query,
                    fused_documents,
                    top_k=config.rag_final_top_k,
                )
                if reranked:
                    documents = []
                    for result in reranked:
                        result.document.metadata["_rerank_score"] = round(result.score, 6)
                        documents.append(result.document)
                    self._log_result(query, dense_count, bm25_count, len(fused_documents), len(documents), "rerank")
                    return documents
            except Exception as error:
                logger.warning(f"Reranker 失败，降级使用 RRF 结果: {error}")

        documents = fused_documents[: config.rag_final_top_k]
        self._log_result(query, dense_count, bm25_count, len(fused_documents), len(documents), "rrf")
        return documents

    @staticmethod
    def _rrf_score(rank: int) -> float:
        return 1 / (config.rag_rrf_k + rank)

    @staticmethod
    def _log_result(
        query: str,
        dense_count: int,
        bm25_count: int,
        fused_count: int,
        final_count: int,
        strategy: str,
    ) -> None:
        logger.info(
            f"混合检索完成: query='{query}', dense={dense_count}, bm25={bm25_count}, "
            f"fused={fused_count}, final={final_count}, strategy={strategy}"
        )


hybrid_retrieval_service = HybridRetrievalService()
