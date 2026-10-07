"""拒答门禁单元测试：三分支判定 + 结构化拒答 + 审计落盘 + 报告形态出口。"""

import json
import time

from ecom_copilot.agents.report_generation import _gate_refusal, _refusal_state
from ecom_copilot.config import get_settings
from ecom_copilot.safety import (
    HOTLINE,
    build_refusal,
    log_rejection,
    should_refuse,
    to_text,
)
from ecom_copilot.schemas.research import Hypothesis, ResearchTask


# ───────────────────────── should_refuse 三分支 ─────────────────────────


def test_should_refuse_no_evidence():
    assert should_refuse(0, None) == (True, "no_evidence")
    assert should_refuse(0, 0.9) == (True, "no_evidence")  # 证据为空时无条件拒答


def test_should_refuse_low_confidence():
    threshold = get_settings().reject_confidence_threshold
    assert should_refuse(5, threshold - 0.01) == (True, "low_confidence")
    assert should_refuse(5, 0.0) == (True, "low_confidence")


def test_should_refuse_pass():
    threshold = get_settings().reject_confidence_threshold
    assert should_refuse(5, threshold + 0.01) == (False, "")
    assert should_refuse(5, 0.9) == (False, "")
    assert should_refuse(3, None) == (False, "")  # 无置信度信息时不误杀


# ───────────────────────── 结构化拒答 ─────────────────────────


def test_build_refusal_structure():
    refusal = build_refusal("友商 XX 膜的价格", "no_evidence")
    assert refusal["refusal"] is True
    assert refusal["reason"] == "no_evidence"
    assert refusal["hotline"] == HOTLINE == "400-680-9527"
    assert "转人工" in refusal["escalate"]
    assert "400-680-9527" in refusal["escalate"]
    assert refusal["missing_info"]
    assert "友商 XX 膜" in refusal["message"]


def test_to_text_rendering():
    text = to_text(build_refusal("C5 能不能用在 Mate X5 上", "low_confidence", detail="口径分歧"))
    assert "C5 能不能用在 Mate X5 上" in text
    assert "400-680-9527" in text
    assert "转人工" in text
    assert "口径分歧" in text


def test_log_rejection_writes_audit():
    log_rejection("友商 XX 膜的价格", "no_evidence", task_id="task-r1")
    path = get_settings().audit_dir / "rejections.jsonl"
    assert path.exists()
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").strip().splitlines() if line]
    last = rows[-1]
    assert last["question"] == "友商 XX 膜的价格"
    assert last["reason"] == "no_evidence"
    assert last["task_id"] == "task-r1"
    assert "ts" in last


# ───────────────────────── report_generation 门禁出口 ─────────────────────────


def test_gate_refusal_no_evidence():
    assert _gate_refusal({"evidence_pool": []}) == "no_evidence"


def test_gate_refusal_low_confidence():
    # deep_research 链路的整体置信度 = 核验假设置信度均值
    state = {"evidence_pool": [object()],
             "hypotheses": [Hypothesis(confidence=0.1), Hypothesis(confidence=0.2)]}
    assert _gate_refusal(state) == "low_confidence"


def test_gate_refusal_pass():
    state = {"evidence_pool": [object(), object()],
             "hypotheses": [Hypothesis(confidence=0.8)]}
    assert _gate_refusal(state) == ""
    # 无假设时确定性兜底置信度 0.4 ≥ 默认阈值 0.3 → 不拒答
    assert _gate_refusal({"evidence_pool": [object()], "hypotheses": []}) == ""


def test_refusal_state_report_shape():
    """拒答构造为报告形态 state 更新：标题「暂时无法回答」，不产生引用。"""
    task = ResearchTask(question="友商 XX 膜的价格")
    update = _refusal_state({"evidence_pool": [], "trace": [], "metrics": {}},
                            task, "no_evidence", time.perf_counter())
    report = update["report"]
    assert report.title == "暂时无法回答"
    assert update["citations"] == []
    assert update["citations"] == report.citations
    assert update["metrics"]["refusal_reason"] == "no_evidence"
    assert report.executive_summary  # 拒答声明非空
    assert any("400-680-9527" in r for r in report.recommendations)
    assert report.sections and report.sections[0].citation_indices == []
    assert "400-680-9527" in report.sections[0].content
