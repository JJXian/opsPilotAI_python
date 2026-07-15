"""DashScope Reranker 服务 - 对混合召回候选进行语义精排。"""

from dataclasses import dataclass

from dashscope import TextReRank
from langchain_core.documents import Document
from loguru import logger

from app.config import config


@dataclass(frozen=True)
class RerankResult:
    document: Document
    score: float


class RerankerService:
    """使用 DashScope 文本重排序模型进行 Cross-Encoder 精排。"""

    def rerank(self, query: str, documents: list[Document], top_k: int) -> list[RerankResult]:
        if not documents:
            return []
        if not config.dashscope_api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用 Reranker")

        response = TextReRank.call(
            model=config.rag_rerank_model,
            query=query,
            documents=[document.page_content for document in documents],
            return_documents=False,
            top_n=min(top_k, len(documents)),
            api_key=config.dashscope_api_key,
        )
        if getattr(response, "status_code", 500) != 200:
            raise RuntimeError(
                f"Reranker 调用失败: {getattr(response, 'code', 'unknown')} - "
                f"{getattr(response, 'message', 'unknown error')}"
            )

        results = []
        for item in response.output.results or []:
            document_index = int(item.index)
            if 0 <= document_index < len(documents):
                results.append(
                    RerankResult(
                        document=documents[document_index],
                        score=float(item.relevance_score),
                    )
                )
        logger.info(
            f"Reranker 精排完成: model={config.rag_rerank_model}, "
            f"候选数={len(documents)}, 返回数={len(results)}"
        )
        return results


reranker_service = RerankerService()
