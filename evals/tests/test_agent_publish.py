"""评测发布时的报告归属、未知状态和公开字段边界。"""
import json

import pytest

from evals.agent_eval.cases import AGENT_BENCHMARK_CASES
from evals.agent_eval.contracts import REPORT_SCHEMA_VERSION, expectation_notes
from evals.agent_eval.publish import project_report
from evals.agent_eval.reporting import fingerprint, group_metrics
from evals.center import datasets


def record():
    return {
        "schema_version": REPORT_SCHEMA_VERSION, "run_id": "report_01", "timestamp": "20261120_131244_000001", "mode": "live",
        "dataset_sha256": "a" * 64, "judges_sha256": "b" * 64,
        "gate": {"passed": False}, "metrics": {**group_metrics([]), "selected": 1, "failed": 1}, "case_results": [{
            "expectation_notes": [], "expected_tools": [], "forbidden_tools": [], "expected_parameters": [], "steps": None, "steps_complete": None,
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
        name="private-name", category="private-category", expectation_notes=["private-note"], expected_tools=["search_web"], forbidden_tools=["search_amazon"],
        expected_parameters=[{"tool_name": "search_web", "keys": ["query", "private-key"]}],
        steps_complete=True, steps=[{"tool_name": "search_amazon", "parameters": {"query": "private-query"},
                                   "status": "ok", "step": 2, "parameter_keys": ["private-key", "query"]}],
    )
    run, rows = project_report(raw)
    assert "private-" not in json.dumps([run, rows], default=str)
    assert run["failed_cases"] == 1 and run["metrics"]["parameter_accuracy"]["rate"] is None
    assert rows[0]["expected"] == ["必须调用 search_web", "禁止派发 search_amazon", "search_web 需核对参数 query"]
    assert rows[0]["observed"][0]["parameter_keys"] == ["query"]
    assert rows[0]["verdicts"][0]["issues"][0]["step"] == 2
    assert rows[0]["verdicts"][0]["issues"][0]["reason"] == "实际调用了禁用工具"


def test_batch_metrics_and_judge_summary_are_projected():
    """批次指标与判定器汇总必须由逐例结果直接推导。"""
    run, rows = project_report(record())
    assert run["dataset_name"] == "agent_research"
    assert run["dataset_id"] == "agent_research-v0_legacy"
    assert run["dataset_version"] == "v0_legacy" and run["dataset_archived"] is True
    assert run["dataset_content_sha256"] == ""
    assert run["pass_rate"] == 0 and run["mean_score"] == 0
    assert run["total_tokens"] is None and run["latency_p50_ms"] is None
    assert run["judges"] == [
        {"judge": "ToolBoundaryJudge", "dimension": "governance", "passed": 0, "failed": 1, "unassessable": 0}
    ]
    assert rows[0]["status"] == "failed"


def test_verified_dataset_version_binds_registered_projection():
    """内嵌数据集描述符与内容摘要一致时归入 V1，并标记可比。"""
    version = datasets.current_version("agent_research")
    definition = datasets.suite_definition(version, "agent_research")
    raw = record()
    raw.update(dataset_sha256=definition["content_sha256"],
               dataset={"version": version, "suite": "agent_research",
                        "content_sha256": definition["content_sha256"]})
    run, _ = project_report(raw)
    assert run["dataset_id"] == f"agent_research-{version}"
    assert run["dataset_version"] == version and run["dataset_archived"] is False
    assert run["dataset_content_sha256"] == definition["content_sha256"]
    assert run["dataset_case_count"] == definition["case_count"]
    assert len(run["cases_payload"]) == definition["case_count"]
    assert run["metrics"]["provenance"]["comparable"] is True


def test_unverified_dataset_is_archived_and_incomparable():
    run, _ = project_report(record())
    assert run["metrics"]["provenance"]["comparable"] is False
    assert run["dataset_metadata"]["legacy"] is True


def test_unobserved_steps_do_not_bind_a_synthetic_trace():
    raw = record()
    raw.update(mode="synthetic_trace_regrade", transcript_sha256="0" * 64)
    raw["case_results"][0]["trace_id"] = "c" * 32
    _, rows = project_report(raw)
    assert all(rows[0][key] is None for key in ("trace_id", "observed", "steps_complete"))


def test_registered_case_preserves_only_the_reported_versioned_notes():
    raw = record()
    case = next(item for item in AGENT_BENCHMARK_CASES if item.get("reference_facts"))
    row = raw["case_results"][0]
    notes = expectation_notes(case)
    row.update(case_id=case["id"], case_sha256=fingerprint(case), expectation_notes=notes)
    assert project_report(raw)[1][0]["expected"] == notes
    row["expectation_notes"] = ["private-note"]
    with pytest.raises(ValueError, match="预期说明"):
        project_report(raw)


def test_report_counts_and_ids_are_validated():
    raw = record()
    raw["case_results"].append(raw["case_results"][0])
    raw["metrics"]["selected"] = 2
    with pytest.raises(ValueError):
        project_report(raw)


@pytest.mark.parametrize("field", ["expectation_notes", "expected_tools", "forbidden_tools", "expected_parameters", "steps", "steps_complete"])
def test_incomplete_report_is_rejected(field):
    raw = record()
    del raw["case_results"][0][field]
    with pytest.raises(ValueError):
        project_report(raw)


def test_incorrect_counts_old_versions_and_mixed_sources_are_rejected():
    raw = record()
    with pytest.raises(ValueError, match="状态数量"):
        project_report({**raw, "metrics": {**raw["metrics"], "failed": 0, "unassessable": 1}})
    with pytest.raises(ValueError, match="版本"):
        project_report({**raw, "schema_version": 2})
    with pytest.raises(ValueError, match="合成回放来源"):
        project_report({**raw, "counterexamples_sha256": "3" * 64})
    with pytest.raises(ValueError, match="回放记录摘要"):
        project_report({**raw, "mode": "synthetic_trace_regrade"})


def test_inconsistent_denominators_are_rejected():
    raw = record()
    raw["metrics"]["parameter_accuracy"] = {"numerator": 3, "denominator": 2}
    with pytest.raises(ValueError):
        project_report(raw)


def test_provenance_retains_only_validated_source_digests():
    raw = record()
    digests = {"transcript_sha256": "1" * 64, "counterexamples_sha256": "2" * 64, "model_config_sha256": "3" * 64}
    raw.update(digests, mode="synthetic_trace_regrade", raw_model_configuration="private-source")
    run, _ = project_report(raw)
    assert run["metrics"]["provenance"] == {**digests, "comparable": False}
    assert project_report(record())[0]["metrics"]["provenance"] == {"comparable": False}


@pytest.mark.parametrize("key", ["transcript_sha256", "counterexamples_sha256", "model_config_sha256"])
def test_invalid_source_digest_rejects_report(key):
    with pytest.raises(ValueError):
        project_report({**record(), key: "private-source"})