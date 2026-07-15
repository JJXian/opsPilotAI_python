from pathlib import Path

from app.evaluation.dataset import load_evaluation_dataset
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


def test_default_ops_evaluation_dataset_has_balanced_fifty_samples():
    project_root = Path(__file__).resolve().parents[1]
    samples = load_evaluation_dataset(
        project_root / "evaluation" / "datasets" / "ops_retrieval_eval.json"
    )

    assert len(samples) == 50
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
    }
