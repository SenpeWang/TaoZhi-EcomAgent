"""评测发布时的报告归属、未知状态和公开字段边界。"""
import json

import pytest

from evals.agent_eval.publish import project_report


def record():
    return {
        "schema_version": 2, "run_id": "report_01", "timestamp": "20261120_131244_000001", "mode": "live",
        "dataset_sha256": "a" * 64, "judges_sha256": "b" * 64, "total_cases": 1, "passed_cases": 0,
        "gate": {"passed": False}, "metrics": {}, "case_results": [{
            "case_id": "SYN-1", "case_sha256": "c" * 64, "suite": "regression", "status": "failed",
            "score": 0, "hard_failure": True, "verdicts": [{
                "judge": "ToolBoundaryJudge", "dimension": "governance", "status": "failed", "failure_type": "tool_forbidden",
                "reason": "private-answer", "issues": [{"failure_type": "tool_forbidden", "tool_name": "search_amazon", "step": 2}],
            }],
        }],
    }


def test_publish_projection_only_accepts_public_fields():
    raw = record()
    raw.update(owner_id="private-owner", tenant_id="private-tenant")
    raw["case_results"][0].update(
        name="private-name", category="private-category", expected_tools=["search_web"], forbidden_tools=["search_amazon"],
        expected_parameters=[{"tool_name": "search_web", "keys": ["query", "private-key"]}],
        steps_complete=True, steps=[{"tool_name": "search_amazon", "parameters": {"query": "private-query"},
                                   "status": "ok", "step": 2, "parameter_keys": ["private-key", "query"]}],
    )
    run, rows = project_report(raw)
    assert "private-" not in json.dumps([run, rows], default=str)
    assert run["failed_cases"] == 1 and run["metrics"]["parameter_accuracy"] is None
    assert rows[0]["expected"] == ["必须调用 search_web", "禁止派发 search_amazon", "search_web 需核对参数 query"]
    assert rows[0]["observed"][0]["parameter_keys"] == ["query"]
    assert rows[0]["verdicts"][0]["issues"][0]["step"] == 2


def test_historical_report_preserves_unknown_and_does_not_bind_synthetic_trace():
    raw = record()
    raw["mode"] = "synthetic_trace_regrade"
    raw["case_results"][0]["trace_id"] = "c" * 32
    _, rows = project_report(raw)
    assert all(rows[0][key] is None for key in ("trace_id", "observed", "expected", "steps_complete"))


def test_report_counts_and_ids_are_validated():
    raw = record()
    raw["case_results"].append(raw["case_results"][0])
    raw["total_cases"] = 2
    with pytest.raises(ValueError):
        project_report(raw)


def test_inconsistent_denominators_are_rejected():
    raw = record()
    raw["metrics"]["parameter_accuracy"] = {"numerator": 3, "denominator": 2}
    with pytest.raises(ValueError):
        project_report(raw)
