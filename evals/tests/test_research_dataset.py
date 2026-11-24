"""验证合成调研集、工具原始证据和逐例反例在正式判定入口上一致。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from evals.agent_eval.cases import AGENT_BENCHMARK_CASES
from evals.agent_eval.contracts import validate_cases
from evals.agent_eval.counterexamples import apply_counterexample
from evals.agent_eval.harness import AgentEvalHarness, replay_transcript
from evals.agent_eval.judges import evaluate_case
from evals.agent_eval.runner import read_synthetic_replay

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
CASES = {case["id"]: case for case in AGENT_BENCHMARK_CASES if case.get("execution_mode") == "synthetic_trace_regrade"}
OBSERVATIONS, _ = read_synthetic_replay(DATASETS / "v3_20261124/agent/research_replay.json", set(CASES))
COUNTEREXAMPLES = json.loads((DATASETS / "v3_20261124/agent/research_counterexamples.json").read_text(encoding="utf-8"))
VARIANTS = {row["case_id"]: row for row in COUNTEREXAMPLES["counterexamples"]}


def issues(case: dict, observation: dict) -> set[str]:
    checks = evaluate_case(case, replay_transcript(case, observation))
    return {issue["failure_type"] for check in checks for issue in check.details["issues"]}


def test_research_dataset_catalog_and_coverage():
    validate_cases(AGENT_BENCHMARK_CASES)
    assert CASES.keys() == OBSERVATIONS.keys() == VARIANTS.keys()
    assert len(VARIANTS) == len(COUNTEREXAMPLES["counterexamples"])
    assert COUNTEREXAMPLES["source"] == "synthetic"
    assert COUNTEREXAMPLES["schema_version"] == 1
    assert {case["suite"] for case in CASES.values()} == {"benchmark", "challenge"}
    from evals import naming
    categories = {case["category"] for case in CASES.values()}
    assert categories >= {
        "task_completion", "tool_correctness", "groundedness", "plan_adherence",
        "multi_turn", "prompt_injection", "harmful_task_refusal", "temporal_reasoning",
    }
    for case in CASES.values():
        assert case["capability"] in naming.AGENT_CAPABILITIES
        assert case["category"] in naming.AGENT_SUBCATEGORIES[case["capability"]]
        assert case["track"] in naming.TRACKS


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_synthetic_sources_and_both_verdicts(case_id):
    case, observation, variant = CASES[case_id], OBSERVATIONS[case_id], VARIANTS[case_id]
    returned_ids = set()
    search_urls = set()
    for call in sorted(observation["tool_calls"], key=lambda value: value["sequence"]):
        assert call["data_count"] == len(call["items"])
        assert bool(call["items"]) == (call["status"] == "ok")
        if call["tool_name"] == "fetch_web_page":
            assert call["parameters"]["url"] in search_urls
        for item in call["items"]:
            assert item["evidence_id"].startswith("SYN-") and item["text"].strip()
            assert item["evidence_id"] not in returned_ids
            returned_ids.add(item["evidence_id"])
            if "url" in item:
                assert urlsplit(item["url"]).hostname == "research.invalid"
                if call["tool_name"] == "search_web":
                    search_urls.add(item["url"])
    assert set(observation["evidence_ids"]) == returned_ids
    assert {ref for fact in case["reference_facts"] for ref in fact.get("evidence_ids", [])} <= returned_ids
    assert not issues(case, observation)
    original = copy.deepcopy(observation)
    changed = apply_counterexample(observation, variant)
    assert variant["expected_failure"] in issues(case, changed)
    assert original == observation


def test_fixture_revision_is_checked_at_every_replay_entry():
    case = next(iter(CASES.values()))
    original = OBSERVATIONS[case["id"]]
    with pytest.raises(ValueError, match="版本"):
        replay_transcript(case, {**original, "fixture_version": True})
    with pytest.raises(ValueError, match="版本"):
        apply_counterexample(original, {**VARIANTS[case["id"]], "fixture_version": 2})
    with pytest.raises(ValueError, match="回放"):
        AgentEvalHarness(execute=lambda **_: pytest.fail("触发了真实执行")).run(case)


def test_additional_source_is_optional_but_checked_when_used():
    case = CASES["AGENT-MODEL-016"]
    observation = copy.deepcopy(OBSERVATIONS[case["id"]])
    optional = observation["tool_calls"].pop()
    observation["evidence_ids"].remove("SYN-P8-PRO")
    assert not issues(case, observation)
    optional.update(status="timeout", items=[], data_count=0)
    observation["tool_calls"].append(optional)
    assert "tool_status" in issues(case, observation)
    query = observation["tool_calls"][0]["parameters"]
    for value in ["SYN-P80", "SYN-P8-PRO"]:
        query["query"] = value
        assert "tool_arguments" in issues(case, observation)
    query["query"] = "请检索 syn-p8 标准版的充电规格"
    assert "tool_arguments" not in issues(case, observation)


def test_separate_optional_tool_is_excluded_from_required_coverage():
    case = CASES["AGENT-MODEL-016"]
    rule = case["tool_expectations"][-1]
    standalone = {**case, "tool_expectations": [rule], "tool_order": [], "reference_facts": []}
    run = replay_transcript(case, {**OBSERVATIONS[case["id"]], "tool_calls": []})
    checks = evaluate_case(standalone, run)
    tool_check = next(check for check in checks if check.judge_name == "ToolExecutionJudge")
    assert tool_check.passed
    assert tool_check.details["required_tool_total"] == 0


def test_missing_steering_event_cannot_prove_a_model_change():
    case = CASES["AGENT-SWITCH-017"]
    observation = dict(OBSERVATIONS[case["id"]])
    del observation["steering_after_sequence"]
    assert "missing_observation" in issues(case, observation)


@pytest.mark.parametrize(("case_id", "explanation"), [
    ("AGENT-EMPTY-014", "不能据此断言该商品不存在。"),
    ("AGENT-FX-012", "由于成本未知，无法认定每件净赚 28 元。"),
])
def test_cautious_explanation_is_accepted(case_id, explanation):
    original = OBSERVATIONS[case_id]
    assert not issues(CASES[case_id], {**original, "report": original["report"] + "\n\n" + explanation})


def test_counterexample_can_only_replace_an_existing_observation_value():
    case_id = "AGENT-STOCK-023"
    variant = VARIANTS[case_id]
    for path in (["case_id"], ["tool_calls", -1], ["tool_calls", 5], ["missing"]):
        with pytest.raises(ValueError):
            apply_counterexample(OBSERVATIONS[case_id], {**variant, "edit": {"path": path, "value": "timeout"}})
