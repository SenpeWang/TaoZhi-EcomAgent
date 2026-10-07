"""hypothesis_tracker 状态机与置信度评估测试（商品适配 / 参数口径类假设）。"""

import pytest

from ecom_copilot.hypothesis import HypothesisTracker
from ecom_copilot.schemas.common import Evidence, Sentiment
from ecom_copilot.schemas.research import HypothesisStatus


def _evidence(quote: str, sentiment: Sentiment,
              doc: str = "机型适配总表_钢化膜与水凝膜") -> Evidence:
    return Evidence(doc_name=doc, page=3, quote=quote, sentiment=sentiment, confidence=0.8)


def test_state_machine_transitions():
    tracker = HypothesisTracker("t1")
    hyp = tracker.create("C3 防窥膜未来将兼容曲面屏机型")
    assert hyp.status == HypothesisStatus.PENDING

    tracker.start_investigation(hyp)
    assert hyp.status == HypothesisStatus.INVESTIGATING

    # 非法迁移：pending -> confirmed
    with pytest.raises(Exception):
        tracker.transition(tracker.create("另一假设"), HypothesisStatus.CONFIRMED)


def test_confirm_with_support_evidence():
    tracker = HypothesisTracker("t2")
    hyp = tracker.create("C5 陶瓷膜可适配 Mate X5")
    tracker.start_investigation(hyp)
    tracker.add_evidence(hyp, [
        _evidence("机型适配总表载明 C5 适配华为 Mate X5 与三星 Z Fold5", Sentiment.SUPPORT),
        _evidence("C5 柔性陶瓷膜 0.15mm 全胶，适配折叠屏机型", Sentiment.SUPPORT),
    ])
    tracker.evaluate(hyp)
    assert hyp.status == HypothesisStatus.CONFIRMED
    assert hyp.confidence > 0.6
    assert hyp.needs_human_review is False


def test_refute_with_negative_evidence():
    tracker = HypothesisTracker("t3")
    hyp = tracker.create("C3 防窥膜可适配曲面屏机型")
    tracker.start_investigation(hyp)
    tracker.add_evidence(hyp, [
        _evidence("机型适配总表载明 C3 仅适配 iPhone 14~17 直板机型", Sentiment.REFUTE),
        _evidence("C3 参数页明确标注曲面屏机型不支持", Sentiment.REFUTE),
    ])
    tracker.evaluate(hyp)
    assert hyp.status == HypothesisStatus.REFUTED


def test_conflict_needs_human_review():
    tracker = HypothesisTracker("t4")
    hyp = tracker.create("C1 钢化膜实际硬度达到 9H 标称口径")
    tracker.start_investigation(hyp)
    tracker.add_evidence(hyp, [
        _evidence("钢化膜系列规格书标称 C1 硬度 9H", Sentiment.SUPPORT),
        _evidence("用户实测出现膜边易碎，与标称口径不符", Sentiment.REFUTE),
    ])
    tracker.evaluate(hyp)
    assert hyp.status == HypothesisStatus.INCONCLUSIVE
    assert hyp.needs_human_review is True


def test_insufficient_evidence_keeps_investigating():
    tracker = HypothesisTracker("t5")
    hyp = tracker.create("P1 贴膜神器或将适配更多膜类")
    tracker.start_investigation(hyp)
    tracker.add_evidence(hyp, [_evidence("一条背景证据", Sentiment.NEUTRAL)])
    tracker.evaluate(hyp)
    assert hyp.status == HypothesisStatus.INVESTIGATING


def test_evidence_dedup_and_summary():
    tracker = HypothesisTracker("t6")
    hyp = tracker.create("假设：P1 贴膜神器可通用全系列膜类")
    tracker.add_evidence(hyp, [
        _evidence("同一条证据", Sentiment.SUPPORT),
        _evidence("同一条证据", Sentiment.SUPPORT),
        _evidence("另一条证据", Sentiment.SUPPORT),
    ])
    assert len(hyp.evidence) == 2
    rows = tracker.summary_rows()
    assert rows and rows[0]["status"] == "pending"
