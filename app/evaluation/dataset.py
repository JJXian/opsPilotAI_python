"""评测集加载与校验。"""

import json
from dataclasses import dataclass
from pathlib import Path


def infer_category(sample_id: str) -> str:
    """兼容旧评测集：未显式标注类别时，从稳定的样本 ID 前缀推断。"""
    parts = sample_id.split("-")
    return "_".join(parts[:2] if parts and parts[0] == "exact" else parts[:1])


@dataclass(frozen=True)
class EvaluationSample:
    """一条可同时用于检索与生成评测的人工标注样本。"""

    id: str
    question: str
    reference_answer: str
    expected_sources: tuple[str, ...]
    category: str = "uncategorized"
    key_facts: tuple[str, ...] = ()
    answerable: bool = True
    should_clarify: bool = False


def load_evaluation_dataset(dataset_path: Path) -> list[EvaluationSample]:
    """加载 JSON 评测集，并尽早发现不完整标注。"""
    raw_samples = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(raw_samples, list):
        raise ValueError("评测集必须是 JSON 数组")

    samples: list[EvaluationSample] = []
    for index, raw_sample in enumerate(raw_samples, start=1):
        if not isinstance(raw_sample, dict):
            raise ValueError(f"第 {index} 条样本必须是 JSON 对象")

        required_fields = ("id", "question", "reference_answer")
        missing = [field for field in required_fields if not raw_sample.get(field)]
        if "expected_sources" not in raw_sample:
            missing.append("expected_sources")
        if missing:
            raise ValueError(f"第 {index} 条样本缺少字段: {', '.join(missing)}")

        expected_sources = raw_sample["expected_sources"]
        if not isinstance(expected_sources, list) or not all(
            isinstance(source, str) and source.strip() for source in expected_sources
        ):
            raise ValueError(f"第 {index} 条样本 expected_sources 必须是字符串数组")

        answerable = raw_sample.get("answerable", True)
        should_clarify = raw_sample.get("should_clarify", False)
        if not isinstance(answerable, bool) or not isinstance(should_clarify, bool):
            raise ValueError(f"第 {index} 条样本 answerable/should_clarify 必须是布尔值")
        if answerable and not expected_sources:
            raise ValueError(f"第 {index} 条可回答样本 expected_sources 不能为空")

        key_facts = raw_sample.get("key_facts", [])
        if not isinstance(key_facts, list) or not all(
            isinstance(fact, str) and fact.strip() for fact in key_facts
        ):
            raise ValueError(f"第 {index} 条样本 key_facts 必须是字符串数组")

        sample_id = str(raw_sample["id"])
        samples.append(
            EvaluationSample(
                id=sample_id,
                question=str(raw_sample["question"]),
                reference_answer=str(raw_sample["reference_answer"]),
                expected_sources=tuple(expected_sources),
                category=str(raw_sample.get("category") or infer_category(sample_id)),
                key_facts=tuple(key_facts),
                answerable=answerable,
                should_clarify=should_clarify,
            )
        )
    return samples
