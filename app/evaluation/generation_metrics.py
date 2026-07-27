"""RAG 生成质量、引用质量与端到端通过规则。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from langchain_qwq import ChatQwen
from pydantic import BaseModel, Field

from app.config import config
from app.evaluation.dataset import EvaluationSample

SOURCE_PATTERN = re.compile(r"【来源：([^】]+)】")


class GenerationJudgeDecision(BaseModel):
    """结构化 LLM 裁判结果，所有分数都使用 0~1。"""

    faithfulness: float = Field(ge=0, le=1)
    answer_correctness: float = Field(ge=0, le=1)
    answer_relevancy: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    citation_completeness: float = Field(ge=0, le=1)
    reason: str


class GenerationJudge(Protocol):
    async def judge(
        self,
        sample: EvaluationSample,
        answer: str,
        retrieved_contexts: list[str],
    ) -> GenerationJudgeDecision: ...


class StructuredLLMJudge:
    """使用温度为 0 的结构化模型评估生成质量和引用语义质量。"""

    def __init__(self, max_attempts: int = 3) -> None:
        self.model = ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_key,
            temperature=0,
        )
        self.max_attempts = max_attempts

    async def judge(
        self,
        sample: EvaluationSample,
        answer: str,
        retrieved_contexts: list[str],
    ) -> GenerationJudgeDecision:
        key_facts = list(sample.key_facts) or [sample.reference_answer]
        contexts = "\n\n".join(
            f"【检索片段 {index}】\n{context}"
            for index, context in enumerate(retrieved_contexts, start=1)
        )
        prompt = f"""你是企业知识库 RAG 的严格评测员。请只根据输入材料评分，不得使用外部知识。
所有分数均为 0 到 1：
1. faithfulness：回答中的可验证事实有多少能被检索上下文直接支持。
2. answer_correctness：回答与参考答案及关键事实是否一致，错误数值、路径、错误码按严重错误处理。
3. answer_relevancy：回答是否直接、完整地回应问题。
4. citation_correctness：每个【来源：...】是否真实支持它附近的结论；仅名称存在但内容不支持不能得分。
5. citation_completeness：回答中的关键事实有多少带有能支持它的【来源：...】。

若样本不可回答，正确说明证据不足或提出必要澄清属于正确回答，此时没有引用不扣分。

用户问题：
{sample.question}

参考答案：
{sample.reference_answer}

关键事实：
{key_facts}

是否可回答：{sample.answerable}
是否应该反问：{sample.should_clarify}

检索上下文：
{contexts or "无"}

待评回答：
{answer}
"""
        structured_model = self.model.with_structured_output(GenerationJudgeDecision)
        last_error: Exception | None = None
        for _attempt in range(self.max_attempts):
            try:
                decision = await structured_model.ainvoke(prompt)
                if decision is not None:
                    return decision
                last_error = RuntimeError("裁判模型返回空结构")
            except Exception as error:
                last_error = error
        raise RuntimeError(f"裁判模型连续 {self.max_attempts} 次评分失败: {last_error}")


@dataclass(frozen=True)
class GenerationMetrics:
    faithfulness: float
    answer_correctness: float
    answer_relevancy: float
    citation_correctness: float
    citation_completeness: float
    citation_source_validity: float
    end_to_end_pass: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def normalize_source(source: str) -> str:
    """去掉页码等定位后缀并统一成文件名，兼容回答中的完整路径。"""
    primary = source.split(" / ", maxsplit=1)[0].strip()
    return Path(primary).name.casefold()


def extract_citations(answer: str) -> list[str]:
    """提取并去重回答中的来源标记。"""
    return list(dict.fromkeys(match.strip() for match in SOURCE_PATTERN.findall(answer)))


def calculate_citation_source_validity(
    answer: str,
    retrieved_sources: list[str],
    *,
    answerable: bool,
) -> float:
    """确定性检查：引用来源必须来自本轮实际检索结果。"""
    citations = extract_citations(answer)
    if not citations:
        return 1.0 if not answerable else 0.0
    available = {normalize_source(source) for source in retrieved_sources}
    valid = sum(normalize_source(citation) in available for citation in citations)
    return valid / len(citations)


def combine_generation_metrics(
    decision: GenerationJudgeDecision,
    citation_source_validity: float,
    *,
    faithfulness_threshold: float = 0.9,
    correctness_threshold: float = 0.85,
    relevancy_threshold: float = 0.85,
    citation_threshold: float = 0.9,
) -> GenerationMetrics:
    """组合裁判分数与确定性引用校验，给出严格的端到端通过结果。"""
    passed = all(
        (
            decision.faithfulness >= faithfulness_threshold,
            decision.answer_correctness >= correctness_threshold,
            decision.answer_relevancy >= relevancy_threshold,
            decision.citation_correctness >= citation_threshold,
            decision.citation_completeness >= citation_threshold,
            citation_source_validity == 1.0,
        )
    )
    return GenerationMetrics(
        faithfulness=decision.faithfulness,
        answer_correctness=decision.answer_correctness,
        answer_relevancy=decision.answer_relevancy,
        citation_correctness=decision.citation_correctness,
        citation_completeness=decision.citation_completeness,
        citation_source_validity=citation_source_validity,
        end_to_end_pass=float(passed),
    )


def average_generation_metrics(metrics: list[GenerationMetrics]) -> dict[str, float]:
    if not metrics:
        return dict.fromkeys(GenerationMetrics.__dataclass_fields__, 0.0)
    return {
        field: sum(getattr(metric, field) for metric in metrics) / len(metrics)
        for field in GenerationMetrics.__dataclass_fields__
    }
