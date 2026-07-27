from pathlib import Path

from app.evaluation.dataset import EvaluationSample, load_evaluation_dataset
from app.evaluation.generation_metrics import (
    GenerationJudgeDecision,
    calculate_citation_source_validity,
    combine_generation_metrics,
    extract_citations,
)
from app.evaluation.metrics import average_metrics, calculate_retrieval_metrics, unique_ids


def test_retrieval_metrics_use_source_level_deduplication():
    metrics = calculate_retrieval_metrics(
        ["unrelated.md", "cpu_high_usage.md", "cpu_high_usage.md"],
        ["cpu_high_usage.md"],
    )

    assert unique_ids(["a", "a", "b"]) == ["a", "b"]
    assert metrics.context_precision == 0.5
    assert metrics.context_recall == 1.0
    assert metrics.hit_rate == 1.0
    assert metrics.mrr == 0.5


def test_average_metrics_returns_zero_for_empty_evaluation():
    assert average_metrics([]) == {
        "context_precision": 0.0,
        "context_recall": 0.0,
        "hit_rate": 0.0,
        "mrr": 0.0,
    }


def test_default_ops_evaluation_dataset_has_balanced_eighty_samples():
    project_root = Path(__file__).resolve().parents[1]
    samples = load_evaluation_dataset(
        project_root / "evaluation" / "datasets" / "ops_retrieval_eval.json"
    )

    assert len(samples) == 80
    source_counts = {}
    for sample in samples:
        for source in sample.expected_sources:
            source_counts[source] = source_counts.get(source, 0) + 1

    assert source_counts == {
        "cpu_high_usage.md": 10,
        "memory_high_usage.md": 10,
        "slow_response.md": 10,
        "service_unavailable.md": 10,
        "disk_high_usage.md": 10,
        "precision_retrieval_runbook.md": 30,
    }


def test_citation_source_validity_rejects_invented_source():
    answer = (
        "应先检查监听器状态【来源：precision_retrieval_runbook.md / 第 2 页】，"
        "然后重启服务【来源：invented.md】。"
    )

    assert extract_citations(answer) == [
        "precision_retrieval_runbook.md / 第 2 页",
        "invented.md",
    ]
    assert (
        calculate_citation_source_validity(
            answer,
            ["/uploads/precision_retrieval_runbook.md"],
            answerable=True,
        )
        == 0.5
    )


def test_generation_metrics_apply_strict_end_to_end_gate():
    decision = GenerationJudgeDecision(
        faithfulness=0.95,
        answer_correctness=0.9,
        answer_relevancy=0.9,
        citation_correctness=0.95,
        citation_completeness=0.95,
        reason="关键结论均有证据和引用。",
    )

    passed = combine_generation_metrics(decision, 1.0)
    failed = combine_generation_metrics(decision, 0.5)

    assert passed.end_to_end_pass == 1.0
    assert failed.end_to_end_pass == 0.0


def test_unanswerable_sample_can_have_no_expected_source(tmp_path):
    dataset = tmp_path / "unanswerable.json"
    dataset.write_text(
        """
        [{
          "id": "unknown-01",
          "question": "明年的数据库版本是什么？",
          "reference_answer": "现有知识库无法确认。",
          "expected_sources": [],
          "answerable": false,
          "should_clarify": false
        }]
        """,
        encoding="utf-8",
    )

    sample = load_evaluation_dataset(dataset)[0]

    assert sample == EvaluationSample(
        id="unknown-01",
        question="明年的数据库版本是什么？",
        reference_answer="现有知识库无法确认。",
        expected_sources=(),
        category="unknown",
        answerable=False,
        should_clarify=False,
    )
