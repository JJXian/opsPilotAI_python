"""BM25 词法检索服务 - 从 Milvus 分片快照构建可重建的本地索引。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from threading import RLock
from typing import Any

import jieba
from loguru import logger
from rank_bm25 import BM25Okapi

TECHNICAL_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")


@dataclass(frozen=True)
class Bm25Document:
    """BM25 索引中的分片快照。"""

    id: str
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Bm25SearchResult:
    """BM25 召回结果。"""

    document: Bm25Document
    score: float


class Bm25RetrievalService:
    """中文/英文/技术标识符兼容的本地 BM25 索引。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._index: BM25Okapi | None = None
        self._documents: list[Bm25Document] = []
        self._token_sets: list[set[str]] = []
        self._ready = False

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """中文按词切分，同时保留错误码、服务名、IP 等技术 token。"""
        segmented_tokens = [token.strip().lower() for token in jieba.lcut(text) if token.strip()]
        technical_tokens = [match.group(0).lower() for match in TECHNICAL_TOKEN_PATTERN.finditer(text)]
        return segmented_tokens + technical_tokens or ["<empty>"]

    def refresh(self, chunks: Iterable[tuple[str, str, dict[str, Any]]]) -> int:
        """用当前全部分片原子替换 BM25 快照。"""
        documents = [
            Bm25Document(id=chunk_id, content=content, metadata=dict(metadata))
            for chunk_id, content, metadata in chunks
            if content.strip()
        ]
        tokenized_corpus = [self.tokenize(document.content) for document in documents]
        index = BM25Okapi(tokenized_corpus) if tokenized_corpus else None

        with self._lock:
            self._documents = documents
            self._token_sets = [set(tokens) for tokens in tokenized_corpus]
            self._index = index
            self._ready = True

        logger.info(f"BM25 索引刷新完成: 分片数={len(documents)}")
        return len(documents)

    def refresh_from_milvus(self) -> int:
        """从 Milvus 重建 BM25，保证向量库始终是唯一数据真源。"""
        from app.services.vector_store_manager import vector_store_manager

        return self.refresh(vector_store_manager.list_indexed_chunks())

    def ensure_ready(self) -> None:
        """首次检索时延迟构建索引，避免应用启动时额外阻塞。"""
        with self._lock:
            is_ready = self._ready
        if not is_ready:
            self.refresh_from_milvus()

    def search(self, query: str, top_k: int) -> list[Bm25SearchResult]:
        """按 BM25 分数返回关键词最匹配的分片。"""
        self.ensure_ready()
        query_tokens = self.tokenize(query)

        with self._lock:
            index = self._index
            documents = list(self._documents)
            token_sets = list(self._token_sets)

        if index is None or not documents:
            return []

        scores = index.get_scores(query_tokens)
        ranked_indexes = sorted(range(len(documents)), key=lambda index: scores[index], reverse=True)
        query_token_set = set(query_tokens)
        results = [
            Bm25SearchResult(document=documents[index], score=float(scores[index]))
            for index in ranked_indexes[:top_k]
            # 小语料中单词的 BM25 IDF 可能为 0；只要有词项重合仍应保留候选。
            if query_token_set.intersection(token_sets[index])
            and self._is_retrievable(documents[index].metadata)
        ]
        logger.info(f"BM25 召回完成: query='{query}', 结果数={len(results)}")
        return results

    @staticmethod
    def _is_retrievable(metadata: dict[str, Any]) -> bool:
        from app.services.document_version_service import document_version_service

        return document_version_service.is_active_version(metadata)


bm25_retrieval_service = Bm25RetrievalService()
