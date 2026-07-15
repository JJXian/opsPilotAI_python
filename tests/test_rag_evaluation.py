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
