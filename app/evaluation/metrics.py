"""检索指标计算，以及 RAGAS ID 指标的可选适配。"""

import asyncio
import warnings
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    """单条查询的检索指标。"""

    context_precision: float
    context_recall: float
    hit_rate: float
    mrr: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def unique_ids(ids: list[str] | tuple[str, ...]) -> list[str]:
    """去重且保持原始排序，避免同一来源的多个分片影响来源级指标。"""
    return list(dict.fromkeys(identifier for identifier in ids if identifier))


def calculate_retrieval_metrics(
    retrieved_ids: list[str] | tuple[str, ...],
    reference_ids: list[str] | tuple[str, ...],
) -> RetrievalMetrics:
    """计算来源级 Context Precision、Recall、Hit Rate 与 MRR。"""
    retrieved = unique_ids(retrieved_ids)
    references = set(unique_ids(reference_ids))
    relevant_count = sum(identifier in references for identifier in retrieved)

    precision = relevant_count / len(retrieved) if retrieved else 0.0
    recall = relevant_count / len(references) if references else 0.0
    first_relevant_rank = next(
        (rank for rank, identifier in enumerate(retrieved, start=1) if identifier in references),
        None,
    )
    return RetrievalMetrics(
        context_precision=precision,
        context_recall=recall,
        hit_rate=float(first_relevant_rank is not None),
        mrr=1 / first_relevant_rank if first_relevant_rank else 0.0,
    )


def calculate_ragas_id_metrics(
    retrieved_ids: list[str], reference_ids: list[str]
) -> tuple[RetrievalMetrics, str]:
    """用 RAGAS 的 ID 指标评分；未安装时使用等价的本地公式。"""
    fallback = calculate_retrieval_metrics(retrieved_ids, reference_ids)
    try:
        from ragas.dataset_schema import SingleTurnSample
        # RAGAS 0.4 的 collection API 尚未提供 ID 指标，保留的兼容接口可正确执行。
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, module="ragas.metrics")
            from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    except ImportError:
        return fallback, "local_id_metrics (install: uv sync --extra eval)"

    sample = SingleTurnSample(
        retrieved_context_ids=unique_ids(retrieved_ids),
        reference_context_ids=unique_ids(reference_ids),
    )

    async def score() -> tuple[float, float]:
        precision = await IDBasedContextPrecision().single_turn_ascore(sample)
        recall = await IDBasedContextRecall().single_turn_ascore(sample)
        return float(precision), float(recall)

    try:
        precision, recall = asyncio.run(score())
    except RuntimeError as error:
        # 评测命令是同步 CLI；若未来嵌入已有事件循环的程序，仍可得到稳定的离线结果。
        return fallback, f"local_id_metrics (RAGAS unavailable: {error})"

    return (
        RetrievalMetrics(
            context_precision=precision,
            context_recall=recall,
            hit_rate=fallback.hit_rate,
            mrr=fallback.mrr,
        ),
        "ragas_id_based",
    )


def average_metrics(metrics: list[RetrievalMetrics]) -> dict[str, float]:
    """计算评测集中各指标的平均值。"""
    if not metrics:
        return dict.fromkeys(RetrievalMetrics.__dataclass_fields__, 0.0)
    return {
        field: sum(getattr(metric, field) for metric in metrics) / len(metrics)
        for field in RetrievalMetrics.__dataclass_fields__
    }
