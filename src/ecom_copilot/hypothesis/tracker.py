"""hypothesis_tracker：商品知识核验假设追踪状态机（交叉核验模块核心）。

状态机：pending → investigating → confirmed / refuted / inconclusive
证据来源从「日志 / 指标」变为「商品手册 / 参数表 / 详情页 / FAQ / 客服工单」。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..config import Settings, get_settings
from ..observability import inc, log_event
from ..schemas.common import Evidence, Sentiment
from ..schemas.research import ALLOWED_TRANSITIONS, Hypothesis, HypothesisStatus


class TransitionError(ValueError):
    pass


class HypothesisTracker:
    """假设追踪器：状态机 + 证据链 + 置信度评估 + 人工复核判定。"""

    def __init__(self, task_id: str, settings: Optional[Settings] = None) -> None:
        self.task_id = task_id
        self.settings = settings or get_settings()
        self.hypotheses: Dict[str, Hypothesis] = {}
        self._lock = threading.RLock()
        self._round = 0
        self.persist_path = (
            Path(self.settings.data_dir) / "state" / f"hypotheses-{task_id}.json"
        )
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)

    # ───────── 生命周期 ─────────
    @property
    def current_round(self) -> int:
        return self._round

    def next_round(self) -> int:
        with self._lock:
            self._round += 1
            return self._round

    def create(self, text: str, category: str = "") -> Hypothesis:
        with self._lock:
            hypothesis = Hypothesis(
                task_id=self.task_id,
                hypothesis_text=text.strip(),
                category=category or self._guess_category(text),
                status=HypothesisStatus.PENDING,
                round_created=self._round,
            )
            self.hypotheses[hypothesis.id] = hypothesis
            inc("hypothesis.created")
            log_event("hypothesis_created", task_id=self.task_id, text=text[:80])
            return hypothesis

    def create_many(self, texts: Sequence[str]) -> List[Hypothesis]:
        return [self.create(t) for t in texts if t and t.strip()]

    def get(self, hypothesis_id: str) -> Optional[Hypothesis]:
        return self.hypotheses.get(hypothesis_id)

    def all(self) -> List[Hypothesis]:
        return list(self.hypotheses.values())

    def by_status(self, status: HypothesisStatus) -> List[Hypothesis]:
        return [h for h in self.all() if h.status == status]

    # ───────── 状态迁移 ─────────
    def transition(self, hypothesis: Hypothesis, new_status: HypothesisStatus,
                   reason: str = "") -> Hypothesis:
        allowed = ALLOWED_TRANSITIONS.get(hypothesis.status.value, [])
        if new_status.value not in allowed:
            raise TransitionError(
                f"非法状态迁移：{hypothesis.status.value} -> {new_status.value}"
            )
        hypothesis.transitions.append({
            "from": hypothesis.status.value,
            "to": new_status.value,
            "round": self._round,
            "reason": reason,
            "at": datetime.utcnow().isoformat(),
        })
        hypothesis.status = new_status
        hypothesis.round_updated = self._round
        hypothesis.updated_at = datetime.utcnow()
        inc(f"hypothesis.transition.{new_status.value}")
        return hypothesis

    def start_investigation(self, hypothesis: Hypothesis) -> Hypothesis:
        if hypothesis.status == HypothesisStatus.PENDING:
            self.transition(hypothesis, HypothesisStatus.INVESTIGATING, "开始交叉取证")
        return hypothesis

    # ───────── 证据 ─────────
    def add_evidence(self, hypothesis: Hypothesis, evidence: Sequence[Evidence]) -> Hypothesis:
        with self._lock:
            known = {e.quote[:60] for e in hypothesis.evidence}
            added = 0
            for item in evidence:
                if item.quote[:60] in known:
                    continue
                hypothesis.evidence.append(item)
                known.add(item.quote[:60])
                added += 1
            if added:
                inc("hypothesis.evidence", added)
                hypothesis.updated_at = datetime.utcnow()
        return hypothesis

    # ───────── 评估 ─────────
    @staticmethod
    def compute_confidence(hypothesis: Hypothesis) -> float:
        support = hypothesis.support_count()
        refute = hypothesis.refute_count()
        total = support + refute
        if total == 0:
            base = 0.25
        else:
            base = 0.5 + 0.5 * ((support - refute) / total)
        # 证据数量加权
        weight = min(1.0, 0.55 + 0.12 * total)
        # 溯源覆盖率加权（无出处证据不可信，答案必须可回溯到原文段落）
        coverage = hypothesis.coverage()
        score = base * weight * (0.6 + 0.4 * coverage)
        return round(max(0.0, min(1.0, score)), 3)

    def evaluate(self, hypothesis: Hypothesis, min_evidence: Optional[int] = None) -> Hypothesis:
        """基于证据数量与一致性给出结论（PRD E5/E6）。"""
        min_evidence = min_evidence or self.settings.min_evidence_per_hypothesis
        support = hypothesis.support_count()
        refute = hypothesis.refute_count()
        total = support + refute
        hypothesis.confidence = self.compute_confidence(hypothesis)

        if total < min_evidence:
            hypothesis.reasoning = f"证据不足（{total}/{min_evidence}），需继续交叉取证"
            if hypothesis.status != HypothesisStatus.INVESTIGATING:
                try:
                    self.transition(hypothesis, HypothesisStatus.INVESTIGATING, "证据不足，继续交叉取证")
                except TransitionError:
                    hypothesis.status = HypothesisStatus.INVESTIGATING
            return hypothesis

        net = support - refute
        if net >= 2:
            target = HypothesisStatus.CONFIRMED
            hypothesis.reasoning = f"支持证据 {support} 条 vs 反驳 {refute} 条，结论成立"
        elif net <= -2:
            target = HypothesisStatus.REFUTED
            hypothesis.reasoning = f"反驳证据 {refute} 条 vs 支持 {support} 条，结论被证伪"
        elif abs(net) <= 1 and support > 0 and refute > 0:
            target = HypothesisStatus.INCONCLUSIVE
            hypothesis.reasoning = f"多源证据接近（{support} vs {refute}），存在口径冲突，需人工复核"
        else:
            target = HypothesisStatus.INCONCLUSIVE
            hypothesis.reasoning = "证据不足以形成明确结论"

        try:
            self.transition(hypothesis, target, hypothesis.reasoning)
        except TransitionError:
            hypothesis.status = target

        hypothesis.needs_human_review = (
            hypothesis.confidence < self.settings.confidence_review_threshold
            or target == HypothesisStatus.INCONCLUSIVE
            or hypothesis.coverage() < self.settings.citation_coverage_target
        )
        if hypothesis.needs_human_review:
            inc("hypothesis.human_review")
            log_event("hypothesis_review_required", hypothesis=hypothesis.hypothesis_text[:80],
                      confidence=hypothesis.confidence)
        return hypothesis

    def evaluate_all(self) -> List[Hypothesis]:
        return [self.evaluate(h) for h in self.all()]

    def needs_more_rounds(self) -> bool:
        if self._round >= self.settings.max_hypothesis_rounds:
            return False
        return any(
            h.status in (HypothesisStatus.PENDING, HypothesisStatus.INVESTIGATING)
            for h in self.all()
        )

    def under_investigated(self) -> List[Hypothesis]:
        return [
            h for h in self.all()
            if h.status == HypothesisStatus.INVESTIGATING
            and len(h.evidence) < self.settings.min_evidence_per_hypothesis
        ]

    # ───────── 输出 ─────────
    def summary_rows(self) -> List[Dict[str, object]]:
        rows = []
        for h in self.all():
            key_evidence = ""
            if h.evidence:
                top = max(h.evidence, key=lambda e: e.confidence)
                key_evidence = f"{top.doc_name} P{top.page or '-'}：{top.quote[:60]}"
            rows.append({
                "id": h.id,
                "hypothesis_text": h.hypothesis_text,
                "status": h.status.value,
                "confidence": h.confidence,
                "support": h.support_count(),
                "refute": h.refute_count(),
                "coverage": round(h.coverage(), 3),
                "key_evidence": key_evidence,
                "needs_human_review": h.needs_human_review,
            })
        return rows

    def save(self) -> None:
        with self._lock:
            payload = [h.model_dump(mode="json") for h in self.all()]
            self.persist_path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )

    @staticmethod
    def _guess_category(text: str) -> str:
        for key, label in (("价", "价格"), ("产能", "产能"), ("政策", "政策"),
                           ("技术", "技术"), ("量产", "技术"), ("供需", "供需"),
                           ("出口", "贸易")):
            if key in text:
                return label
        return "综合"


def sentiment_from_text(text: str) -> Sentiment:
    """粗粒度证据情感判定（支持 / 反驳 / 中性）。"""
    positive = ("增长", "提升", "扩产", "突破", "加速", "量产", "利好", "上行", "回升", "提高")
    negative = ("下滑", "下降", "减产", "延期", "受阻", "利空", "下行", "亏损", "承压", "放缓")
    if any(k in text for k in positive):
        return Sentiment.SUPPORT
    if any(k in text for k in negative):
        return Sentiment.REFUTE
    return Sentiment.NEUTRAL
