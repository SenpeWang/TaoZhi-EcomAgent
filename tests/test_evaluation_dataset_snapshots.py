"""数据集内容快照：入库用例校验与运行覆盖规则。"""
from __future__ import annotations

import pytest

from taozhi.application.evaluation import dataset_snapshots as snapshots


def _case(suite: str, case_id: str, payload: dict) -> dict:
    return {"suite": suite, "case_id": case_id,
            "case_sha256": snapshots.case_sha256(payload), "case_payload": payload}


def _row(case: dict) -> dict:
    return {"case_id": case["case_id"], "case_sha256": case["case_sha256"],
            "suite": case["suite"]}


def test_validate_full_rag_requires_both_suites() -> None:
    """RAG full 必须覆盖 core 与 adversarial 两个套件的全部用例。"""
    core = _case("rag_core", "A", {"case_id": "A", "question": "q"})
    adv = _case("rag_adversarial", "Z1", {"id": "Z1", "question": "q"})
    rows = [_row(core), _row(adv)]
    stats = snapshots.validate_report_cases(rows, [core, adv], "rag", "full", "live")
    assert stats["expected"] == 2 and stats["covered"] == 2
    with pytest.raises(ValueError, match="full 口径缺少用例"):
        snapshots.validate_report_cases([_row(core)], [core, adv], "rag", "full", "live")


def test_validate_rejects_unknown_case_and_sha_mismatch() -> None:
    """未知用例与定义摘要不一致必须拒绝入库，不能静默补字段。"""
    case = _case("rag_core", "A", {"case_id": "A"})
    assert snapshots.validate_report_cases([_row(case)], [case], "rag", "quick", "live")
    with pytest.raises(ValueError, match="不存在的用例"):
        snapshots.validate_report_cases([{"case_id": "behavior:X", "case_sha256": "x",
                                          "suite": "behavior"}], [case], "rag", "quick", "live")
    bad = {"case_id": "behavior:A", "case_sha256": "f" * 64, "suite": "behavior"}
    with pytest.raises(ValueError, match="摘要与快照不一致"):
        snapshots.validate_report_cases([bad], [case], "rag", "quick", "live")


def test_validate_agent_full_respects_execution_mode() -> None:
    """Agent full 只要求覆盖与本次执行方式一致的用例。"""
    live = _case("agent_research", "AGENT-1", {"id": "AGENT-1", "execution_mode": "live"})
    replay = _case("agent_research", "AGENT-2",
                   {"id": "AGENT-2", "execution_mode": "synthetic_trace_regrade"})
    stats = snapshots.validate_report_cases([_row(live)], [live, replay], "agent", "full", "live")
    assert stats["expected"] == 1 and stats["covered"] == 1
    with pytest.raises(ValueError, match="full 口径缺少用例"):
        snapshots.validate_report_cases([], [live, replay], "agent", "full", "live")