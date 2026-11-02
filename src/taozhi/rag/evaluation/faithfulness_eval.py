"""独立生成忠实度与幻觉评估器：校验回答断言与切片证据的一致性。"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class FaithfulnessReport(BaseModel):
    """忠实度评估报告。"""
    score: float = 1.0
    total_claims: int = 0
    supported_claims: List[str] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    hallucination_detected: bool = False


class FaithfulnessEvaluator:
    """生成忠实度评估器。"""

    def evaluate(
        self, answer: str, context_chunks: List[Dict[str, Any]]
    ) -> FaithfulnessReport:
        """评估生成回答是否有参考切片确切支撑。"""
        if not answer.strip():
            return FaithfulnessReport(score=0.0)

        # 简单句式切分断言
        sentences = [
            s.strip()
            for s in re.split(r"[。！？\n]", answer)
            if len(s.strip()) > 8
        ]

        if not sentences:
            return FaithfulnessReport(score=1.0, total_claims=0)

        # 合并切片全文
        full_context = " ".join(
            str(c.get("content") or c.get("text") or "")
            for c in context_chunks
        )

        supported: List[str] = []
        unsupported: List[str] = []

        for sent in sentences:
            # 抽取句子中的关键词/实体
            words = [w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_]{2,}", sent)]
            if not words:
                continue

            # 核心词在 context 中的覆盖率
            match_count = sum(1 for w in words if w in full_context)
            coverage = match_count / len(words)

            if coverage >= 0.40:
                supported.append(sent)
            else:
                unsupported.append(sent)

        total = len(supported) + len(unsupported)
        score = round(len(supported) / total, 4) if total > 0 else 1.0
        hallucination = len(unsupported) > 0 and score < 0.70

        return FaithfulnessReport(
            score=score,
            total_claims=total,
            supported_claims=supported,
            unsupported_claims=unsupported,
            hallucination_detected=hallucination,
        )