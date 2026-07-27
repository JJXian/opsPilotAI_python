"""Agentic RAG 生成层与端到端离线评测命令。"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.evaluation.dataset import EvaluationSample, load_evaluation_dataset
from app.evaluation.generation_metrics import (
    GenerationJudge,
    GenerationMetrics,
    StructuredLLMJudge,
    average_generation_metrics,
    calculate_citation_source_validity,
    combine_generation_metrics,
    extract_citations,
)
from app.evaluation.runner import DEFAULT_DATASET, DEFAULT_REPORT_DIR, PROJECT_ROOT
from app.services.agentic_rag_service import AgenticRagService, agentic_rag_service
from app.services.bm25_retrieval_service import bm25_retrieval_service


async def evaluate_sample(
    sample: EvaluationSample,
    service: AgenticRagService,
    judge: GenerationJudge,
) -> tuple[dict, GenerationMetrics]:
    started_at = time.perf_counter()
    result = await service.query_for_evaluation(
        sample.question,
        session_id=f"eval-{sample.id}-{uuid4()}",
        force_rag=True,
    )
    generation_latency = time.perf_counter() - started_at

    answer = str(result["answer"])
    contexts = list(result["retrieved_contexts"])
    retrieved_sources = list(result["retrieved_sources"])
    judge_started_at = time.perf_counter()
    decision = await judge.judge(sample, answer, contexts)
    judge_latency = time.perf_counter() - judge_started_at
    source_validity = calculate_citation_source_validity(
        answer,
        retrieved_sources,
        answerable=sample.answerable,
    )
    metrics = combine_generation_metrics(decision, source_validity)
    return (
        {
            "id": sample.id,
            "category": sample.category,
            "question": sample.question,
            "reference_answer": sample.reference_answer,
            "answerable": sample.answerable,
            "expected_sources": list(sample.expected_sources),
            "retrieved_sources": retrieved_sources,
            "retrieval_attempts": result["retrieval_attempts"],
            "answer": answer,
            "citations": extract_citations(answer),
            "metrics": metrics.to_dict(),
            "judge_reason": decision.reason,
            "generation_latency_seconds": round(generation_latency, 3),
            "judge_latency_seconds": round(judge_latency, 3),
        },
        metrics,
    )


def summarize_by_category(details: list[dict]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[GenerationMetrics]] = defaultdict(list)
    for detail in details:
        grouped[detail["category"]].append(GenerationMetrics(**detail["metrics"]))
    return {
        category: {"sample_count": len(items), **average_generation_metrics(items)}
        for category, items in sorted(grouped.items())
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Agentic RAG 生成与端到端评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 评测集：`{report['dataset']}`",
        f"- 成功评测：{report['evaluated_count']} / {report['requested_count']}",
        f"- 裁判方案：`{report['judge']}`",
        "",
        "## 总体结果",
        "",
        "| Faithfulness | Answer Correctness | Answer Relevancy | Citation Correctness | Citation Completeness | Citation Source Validity | E2E Pass Rate |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {summary['faithfulness']:.3f} | {summary['answer_correctness']:.3f} | "
            f"{summary['answer_relevancy']:.3f} | {summary['citation_correctness']:.3f} | "
            f"{summary['citation_completeness']:.3f} | "
            f"{summary['citation_source_validity']:.3f} | {summary['end_to_end_pass']:.3f} |"
        ),
        "",
        "## 分类别结果",
        "",
        "| 类别 | 样本数 | Faithfulness | Correctness | Relevancy | Citation Correctness | Citation Completeness | E2E Pass Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, values in report["by_category"].items():
        lines.append(
            f"| {category} | {values['sample_count']} | {values['faithfulness']:.3f} | "
            f"{values['answer_correctness']:.3f} | {values['answer_relevancy']:.3f} | "
            f"{values['citation_correctness']:.3f} | {values['citation_completeness']:.3f} | "
            f"{values['end_to_end_pass']:.3f} |"
        )
    lines.extend(["", "## 逐条结果", ""])
    for detail in report["samples"]:
        metrics = detail["metrics"]
        lines.extend(
            [
                f"### {detail['id']} · {detail['question']}",
                "",
                f"- 回答：{detail['answer']}",
                f"- 期望来源：{', '.join(detail['expected_sources']) or '无'}",
                f"- 实际来源：{', '.join(detail['retrieved_sources']) or '无'}",
                f"- 引用：{', '.join(detail['citations']) or '无'}",
                (
                    f"- 分数：Faithfulness={metrics['faithfulness']:.3f}，"
                    f"Correctness={metrics['answer_correctness']:.3f}，"
                    f"Relevancy={metrics['answer_relevancy']:.3f}，"
                    f"Citation Correctness={metrics['citation_correctness']:.3f}，"
                    f"Citation Completeness={metrics['citation_completeness']:.3f}，"
                    f"E2E={'PASS' if metrics['end_to_end_pass'] else 'FAIL'}"
                ),
                f"- 裁判说明：{detail['judge_reason']}",
                "",
            ]
        )
    if report["errors"]:
        lines.extend(["## 执行失败", ""])
        for error in report["errors"]:
            lines.append(f"- **{error['id']}**：{error['error']}")
    return "\n".join(lines)


async def run_async(
    dataset_path: Path,
    report_dir: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    service: AgenticRagService = agentic_rag_service,
    judge: GenerationJudge | None = None,
    checkpoint_path: Path | None = None,
) -> tuple[Path, Path, dict]:
    samples = load_evaluation_dataset(dataset_path)
    selected = samples[offset : offset + limit if limit is not None else None]
    if not selected:
        raise ValueError("所选评测集为空")

    active_judge = judge or StructuredLLMJudge()
    details: list[dict] = []
    errors: list[dict[str, str]] = []
    if checkpoint_path and checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("selected_ids") != [sample.id for sample in selected]:
            raise ValueError("检查点与当前所选样本不一致，请更换 --checkpoint 路径")
        details = list(checkpoint.get("samples", []))
        errors = list(checkpoint.get("errors", []))
        logger.info("从检查点恢复: completed={}, errors={}", len(details), len(errors))

    metrics = [GenerationMetrics(**detail["metrics"]) for detail in details]
    completed_ids = {detail["id"] for detail in details}
    logger.info("开始生成层评测: samples={}", len(selected))

    milvus_manager.connect()
    bm25_retrieval_service.refresh_from_milvus()
    try:
        for index, sample in enumerate(selected, start=1):
            if sample.id in completed_ids:
                logger.info("评测进度 {}/{}: {}（检查点已完成）", index, len(selected), sample.id)
                continue
            logger.info("评测进度 {}/{}: {}", index, len(selected), sample.id)
            try:
                detail, score = await evaluate_sample(sample, service, active_judge)
            except Exception as error:
                logger.exception("样本评测失败: {}", sample.id)
                errors = [item for item in errors if item["id"] != sample.id]
                errors.append({"id": sample.id, "error": str(error)})
            else:
                errors = [item for item in errors if item["id"] != sample.id]
                details.append(detail)
                metrics.append(score)
                completed_ids.add(sample.id)
            if checkpoint_path:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(
                    json.dumps(
                        {
                            "dataset": str(dataset_path),
                            "selected_ids": [item.id for item in selected],
                            "samples": details,
                            "errors": errors,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
    finally:
        milvus_manager.close()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": str(dataset_path.relative_to(PROJECT_ROOT)),
        "requested_count": len(selected),
        "evaluated_count": len(details),
        "judge": f"structured_llm_judge:{config.rag_model}",
        "thresholds": {
            "faithfulness": 0.9,
            "answer_correctness": 0.85,
            "answer_relevancy": 0.85,
            "citation_correctness": 0.9,
            "citation_completeness": 0.9,
            "citation_source_validity": 1.0,
        },
        "summary": average_generation_metrics(metrics),
        "by_category": summarize_by_category(details),
        "samples": details,
        "errors": errors,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"rag_generation_eval_{timestamp}.json"
    markdown_path = report_dir / f"rag_generation_eval_{timestamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path, report


def main() -> None:
    parser = argparse.ArgumentParser(description="执行 Agentic RAG 生成层与端到端评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--checkpoint", type=Path, help="逐条保存并支持断点续跑的检查点文件")
    args = parser.parse_args()
    if args.offset < 0:
        parser.error("--offset 不能小于 0")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须大于 0")
    json_path, markdown_path, _ = asyncio.run(
        run_async(
            args.dataset,
            args.report_dir,
            offset=args.offset,
            limit=args.limit,
            checkpoint_path=args.checkpoint,
        )
    )
    print(f"JSON 报告: {json_path}")
    print(f"Markdown 报告: {markdown_path}")


if __name__ == "__main__":
    main()
