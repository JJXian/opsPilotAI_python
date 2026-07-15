"""纯向量检索与混合检索的离线 A/B 评测命令。"""

import argparse
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.evaluation.dataset import EvaluationSample, load_evaluation_dataset
from app.evaluation.metrics import (
    RetrievalMetrics,
    average_metrics,
    calculate_ragas_id_metrics,
    unique_ids,
)
from app.services.bm25_retrieval_service import bm25_retrieval_service
from app.services.hybrid_retrieval_service import hybrid_retrieval_service
from app.services.vector_search_service import vector_search_service

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "ops_retrieval_eval.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "evaluation" / "reports"


@dataclass(frozen=True)
class RetrievedSource:
    chunk_id: str
    source: str


def source_from_metadata(chunk_id: str, metadata: dict) -> RetrievedSource:
    """从分片元数据提取稳定的来源标识，优先使用上传文件名。"""
    source = metadata.get("_file_name") or Path(str(metadata.get("_source", ""))).name
    return RetrievedSource(chunk_id=chunk_id, source=str(source or chunk_id))


def retrieve_with_vector(query: str, top_k: int) -> list[RetrievedSource]:
    results = vector_search_service.search_similar_documents(query, top_k=top_k)
    return [source_from_metadata(result.id, result.metadata) for result in results]


def retrieve_with_hybrid(query: str, top_k: int) -> list[RetrievedSource]:
    documents: list[Document] = hybrid_retrieval_service.search(query)
    return [
        source_from_metadata(str(document.metadata.get("_chunk_id", "")), document.metadata)
        for document in documents[:top_k]
    ]


@contextmanager
def temporary_final_top_k(top_k: int) -> Iterator[None]:
    """让混合检索与纯向量检索以相同 TopK 比较。"""
    original_top_k = config.rag_final_top_k
    config.rag_final_top_k = top_k
    try:
        yield
    finally:
        config.rag_final_top_k = original_top_k


def evaluate_strategy(
    name: str,
    samples: list[EvaluationSample],
    retrieve: Callable[[str, int], list[RetrievedSource]],
    top_k: int,
) -> tuple[dict, str]:
    """执行单个检索策略，返回逐条明细与聚合指标。"""
    details: list[dict] = []
    metrics: list[RetrievalMetrics] = []
    metric_source = "local_id_metrics"

    for sample in samples:
        retrieved = retrieve(sample.question, top_k)
        retrieved_sources = unique_ids([item.source for item in retrieved])
        score, current_metric_source = calculate_ragas_id_metrics(
            retrieved_sources, list(sample.expected_sources)
        )
        metric_source = current_metric_source
        metrics.append(score)
        details.append(
            {
                "id": sample.id,
                "question": sample.question,
                "expected_sources": list(sample.expected_sources),
                "retrieved_sources": retrieved_sources,
                "metrics": score.to_dict(),
            }
        )

    return (
        {
            "strategy": name,
            "top_k": top_k,
            "summary": average_metrics(metrics),
            "samples": details,
        },
        metric_source,
    )


def render_markdown(report: dict) -> str:
    """将结果转为便于复盘和放进作品集的 Markdown 报告。"""
    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 评测集：`{report['dataset']}`",
        f"- 样本数：{report['sample_count']}",
        f"- 指标实现：`{report['metric_source']}`",
        "",
        "## 汇总对比",
        "",
        "| 策略 | Context Precision | Context Recall | Hit Rate | MRR |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for result in report["strategies"]:
        summary = result["summary"]
        lines.append(
            f"| {result['strategy']} | {summary['context_precision']:.3f} | "
            f"{summary['context_recall']:.3f} | {summary['hit_rate']:.3f} | {summary['mrr']:.3f} |"
        )

    lines.extend(["", "## 逐条结果", ""])
    for result in report["strategies"]:
        lines.extend([f"### {result['strategy']}", ""])
        for sample in result["samples"]:
            metrics = sample["metrics"]
            lines.extend(
                [
                    f"- **{sample['id']}**：{sample['question']}",
                    f"  - 期望来源：{', '.join(sample['expected_sources'])}",
                    f"  - 实际来源：{', '.join(sample['retrieved_sources']) or '无'}",
                    f"  - P={metrics['context_precision']:.3f}，R={metrics['context_recall']:.3f}，"
                    f"MRR={metrics['mrr']:.3f}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def run(dataset_path: Path, report_dir: Path, top_k: int) -> tuple[Path, Path]:
    """运行 A/B 评测并落盘 JSON 与 Markdown 两种报告。"""
    samples = load_evaluation_dataset(dataset_path)
    if not samples:
        raise ValueError("评测集不能为空")

    logger.info(f"开始 RAG 离线评测: samples={len(samples)}, top_k={top_k}")
    milvus_manager.connect()
    bm25_retrieval_service.refresh_from_milvus()
    try:
        vector_result, vector_metric_source = evaluate_strategy(
            "dense_vector", samples, retrieve_with_vector, top_k
        )
        with temporary_final_top_k(top_k):
            hybrid_result, hybrid_metric_source = evaluate_strategy(
                "hybrid_bm25_rrf", samples, retrieve_with_hybrid, top_k
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dataset": str(dataset_path.relative_to(PROJECT_ROOT)),
            "sample_count": len(samples),
            "metric_source": (
                "ragas_id_based"
                if "ragas_id_based" in {vector_metric_source, hybrid_metric_source}
                else "local_id_metrics"
            ),
            "strategies": [vector_result, hybrid_result],
        }
        report_dir.mkdir(parents=True, exist_ok=True)
        json_path = report_dir / f"rag_retrieval_eval_{timestamp}.json"
        markdown_path = report_dir / f"rag_retrieval_eval_{timestamp}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        logger.info(f"RAG 评测完成: JSON={json_path}, Markdown={markdown_path}")
        return json_path, markdown_path
    finally:
        milvus_manager.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="对比纯向量检索与 Hybrid RAG 的离线效果")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="评测集 JSON 路径")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="报告输出目录")
    parser.add_argument("--top-k", type=int, default=config.rag_final_top_k, help="两种策略统一的 TopK")
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k 必须大于 0")

    json_path, markdown_path = run(args.dataset, args.report_dir, args.top_k)
    print(f"JSON 报告: {json_path}")
    print(f"Markdown 报告: {markdown_path}")


if __name__ == "__main__":
    main()
