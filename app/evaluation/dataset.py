"""评测集加载与校验。"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvaluationSample:
    """一条带有人工标注相关来源的检索评测样本。"""

    id: str
    question: str
    reference_answer: str
    expected_sources: tuple[str, ...]


def load_evaluation_dataset(dataset_path: Path) -> list[EvaluationSample]:
    """加载 JSON 评测集，并尽早发现不完整标注。"""
    raw_samples = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(raw_samples, list):
        raise ValueError("评测集必须是 JSON 数组")

    samples: list[EvaluationSample] = []
    for index, raw_sample in enumerate(raw_samples, start=1):
        if not isinstance(raw_sample, dict):
            raise ValueError(f"第 {index} 条样本必须是 JSON 对象")

        required_fields = ("id", "question", "reference_answer", "expected_sources")
        missing = [field for field in required_fields if not raw_sample.get(field)]
        if missing:
            raise ValueError(f"第 {index} 条样本缺少字段: {', '.join(missing)}")

        expected_sources = raw_sample["expected_sources"]
        if not isinstance(expected_sources, list) or not all(
            isinstance(source, str) and source.strip() for source in expected_sources
        ):
            raise ValueError(f"第 {index} 条样本 expected_sources 必须是非空字符串数组")

        samples.append(
            EvaluationSample(
                id=str(raw_sample["id"]),
                question=str(raw_sample["question"]),
                reference_answer=str(raw_sample["reference_answer"]),
                expected_sources=tuple(expected_sources),
            )
        )
    return samples
