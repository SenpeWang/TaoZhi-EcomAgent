"""评测资产及生产诊断回灌边界的离线回归。"""
import json

import pytest

from evals.agent_eval.cases import AGENT_BENCHMARK_CASES
from evals.agent_eval.contracts import validate_cases
from evals.agent_eval.feedback import collect_candidates

CASE = {"id": "TEST", "name": "合成调研用例", "prompt": "调研合成商品"}


def test_catalog_is_valid():
    validate_cases(AGENT_BENCHMARK_CASES)


@pytest.mark.parametrize("extra", [
    {"required_tools": ["search_web"], "forbidden_tools": ["search_web"]},
    {"tool_expectations": [{"tool_name": "search_amazon", "status": "empty", "min_data_count": 1}]},
    {"tool_order": [["search_web", "fetch_web_page"], ["fetch_web_page", "search_web"]]},
    {"tool_expectations": [{"tool_name": "search_web", "paramters": {}}]},
    {"reference_facts": [{"id": "F1", "any_of": []}]},
    {"permission_expectations": ["tenant_isolated"]},
    {"forbidden_claims": [".*"]},
])
def test_invalid_contract_is_rejected_before_running(extra):
    with pytest.raises(ValueError, match="TEST 的评测契约无效"):
        validate_cases([{**CASE, **extra}])


def test_duplicate_case_id_is_rejected():
    with pytest.raises(ValueError, match="重复"):
        validate_cases([CASE, CASE])


def test_feedback_counts_unique_spans_excludes_raw_text_and_distinguishes_outcomes():
    activity = {
        "trace_id": "private-trace", "span_id": "private-span", "attributes": {"prompt": "private-content"},
        "diagnostics": [
            {"code": "tool.timeout", "outcome": "retry", "message": "private-content"},
            {"code": "tool.timeout", "outcome": "failed"},
            {"code": "private-diagnostic", "outcome": "failed"},
        ],
    }
    page = {"items": [activity]}
    result = collect_candidates([page, page, {"items": [dict(activity, span_id="another-span")]}])
    assert result["observed_traces"] == 1
    assert result["observed_spans"] == result["unclassified_spans"] == 2
    assert [(r["outcome"], r["affected_traces"], r["affected_spans"]) for r in result["candidates"]] == [
        ("failed", 1, 2), ("retry", 1, 2),
    ]
    assert "private" not in json.dumps(result)


def test_feedback_rejects_incomplete_activity_export():
    with pytest.raises(ValueError):
        collect_candidates([{"items": [{"span_id": "missing-trace"}]}])
