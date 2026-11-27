"""运行器的合成轨迹重评、隐私边界与发布门禁回归。"""
from __future__ import annotations

import json

import pytest

from evals.agent_eval import runner
from evals.agent_eval.publish import project_report


def case(case_id, **changes):
    """构造评测用例定义。

    Args:
        case_id: 用例标识。
        changes: 需要覆盖的用例字段。
    """
    return {"id": case_id, "name": "合成网页调研", "category": "tool_selection",
            "prompt": "请查询合成商品。", "keyword": "SYN-K9", "required_tools": ["search_web"],
            "required_sections": ["依据"], "min_chars": 2, "forbidden_tools": ["search_amazon"], **changes}


def observation(case_id, *, forbidden=False):
    """构造评测观测记录。

    Args:
        case_id: 对应用例标识。
        forbidden: 是否追加一次禁用工具调用。
    """
    call = {"tool_name": "search_web", "parameters": {"query": "敏感查询原文"},
            "status": "ok", "dispatched": True, "sequence": 1, "data_count": 1}
    tools = [call, {**call, "tool_name": "search_amazon", "sequence": 2}] if forbidden else [call]
    return {"case_id": case_id, "report": "依据：仅供内部的供应商报价", "tool_calls": tools,
            "system_status": "COMPLETED"}


def recording(tmp_path, observations, **overrides):
    """写入回放记录文件并返回路径。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        observations: 回放观测记录列表。
        overrides: 需要覆盖的记录字段。
    """
    path = tmp_path / "recording.json"
    path.write_text(json.dumps({"source": "synthetic", "schema_version": 1,
                                "observations": observations, **overrides}), encoding="utf-8")
    return path


def test_regrade_hard_failure_blocks_passing_average_and_scrubs_content(tmp_path):
    """验证硬失败阻断总体通过并清洗敏感内容。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    cases = [case(f"C{i}") for i in range(1, 6)]
    observations = [observation(c["id"], forbidden=i == 0) for i, c in enumerate(cases)]
    path = recording(tmp_path, observations, model_config={"model": "internal-model-label"})
    report = runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    assert report["mode"] == "synthetic_trace_regrade"
    assert report["metrics"]["task_completion"]["rate"] == 0.8
    assert report["gate"]["passed"] is False
    assert report["gate"]["blocking_case_ids"] == ["C1"]
    assert report["metrics"]["forbidden_executions"] == 1
    assert report["metrics"]["tool_selection_accuracy"] == {
        "numerator": 5, "denominator": 6, "rate": 0.8333, "unknown": 0,
    }
    assert report["metrics"]["parameter_accuracy"]["rate"] is None
    assert report["metrics"]["token_usage"]["measured_prompt_tokens"] is None
    assert report["model_config_sha256"]
    for artifact in (tmp_path / report["mode"]).iterdir():
        content = artifact.read_text()
        assert all(s not in content for s in ("敏感查询原文", "仅供内部的供应商报价", "internal-model-label"))


def test_missing_observations_and_challenge_do_not_inflate_metrics(tmp_path):
    """验证缺失观测与挑战套件不虚增指标。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    cases = [case("C1"), case("C2"), case("C3", suite="challenge")]
    path = recording(tmp_path, [observation("C1"), {**observation("C3"), "report": ""}])
    report = runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    assert report["suites"]["benchmark"]["task_completion"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert report["suites"]["benchmark"]["unassessable"] == 1
    assert report["suites"]["challenge"]["failed"] == 1
    assert report["gate"]["blocking_case_ids"] == ["C2"]
    assert report["case_results"][1]["elapsed"] is None
    assert report["metrics"]["latency_observations"] == 0


def test_known_failure_in_challenge_only_does_not_mask_standard_results(tmp_path):
    """验证挑战套件的已知失败不掩盖标准集结果。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    cases = [case("C1"), case("C2", suite="challenge")]
    path = recording(tmp_path, [observation("C1"), {**observation("C2"), "report": ""}])
    report = runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    assert report["suites"]["challenge"]["task_completion"]["rate"] == 0
    assert report["suites"]["benchmark"]["task_completion"]["rate"] == 1
    assert report["gate"]["passed"] is True


def test_token_counts_report_only_measured_calls(tmp_path):
    """验证 token 统计只覆盖真实测量的调用。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    usage = {"model_calls": 3, "measured_calls": 2, "prompt_tokens": 0, "completion_tokens": 9}
    path = recording(tmp_path, [{**observation("C1"), "token_usage": usage}, observation("C2")])
    report = runner.run_agent_evaluations([case("C1"), case("C2")], replay_file=path, reports_dir=tmp_path)
    tokens = report["metrics"]["token_usage"]
    assert tokens["case_coverage"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert tokens["call_coverage_among_known"] == {"numerator": 2, "denominator": 3, "rate": 0.6667}
    assert tokens["measured_prompt_tokens"] == 0
    assert tokens["measured_completion_tokens"] == 9


def test_reject_incompatible_baseline_before_starting_live_graph(tmp_path, monkeypatch):
    """验证来源不同的基线在启动实时图前被拒绝。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    cases = [case("C1")]
    path = recording(tmp_path, [observation("C1")])
    runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    report_file = next((tmp_path / "synthetic_trace_regrade").glob("agent_eval_report_*.json"))
    monkeypatch.setattr(runner, "AgentEvalHarness", lambda: pytest.fail("来源不同，不能启动执行"))
    with pytest.raises(ValueError, match="执行来源不同"):
        runner.run_agent_evaluations(cases, reports_dir=tmp_path, compare_file=report_file)


@pytest.mark.parametrize("observations,overrides", [
    ([observation("C1")], {"source": "production"}),
    ([observation("C1"), observation("C1")], {}),
    ([observation("UNKNOWN")], {}),
    ([observation("C1")], {"schema_version": True}),
])
def test_reject_untrusted_or_ambiguous_recordings(tmp_path, observations, overrides):
    """验证来源不可信或重复的记录被拒绝。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        observations: ``observations`` 参数化取值。
        overrides: ``overrides`` 参数化取值。
    """
    path = recording(tmp_path, observations, **overrides)
    with pytest.raises(ValueError):
        runner.run_agent_evaluations([case("C1")], replay_file=path, reports_dir=tmp_path)


def test_cli_regrade_never_runs_graph(tmp_path, monkeypatch):
    """验证命令行重评不会创建图执行器。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    monkeypatch.setattr(runner, "AGENT_BENCHMARK_CASES", [case("C1"), case("C2")])
    monkeypatch.setattr(runner, "AgentEvalHarness", lambda: pytest.fail("重评不应创建图执行器"))
    path = recording(tmp_path, [observation("C1")])
    assert runner.main(["--case", "C1", "--replay", str(path), "--output-dir", str(tmp_path)]) == 0
    assert runner.main(["--replay", str(path), "--output-dir", str(tmp_path)]) == 1


def test_synthetic_case_cannot_reach_a_live_model(tmp_path, monkeypatch):
    """验证固定合成用例不能触达真实模型。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    fixed = case("FIXED", execution_mode="synthetic_trace_regrade", fixture_version=1)
    monkeypatch.setattr(runner, "AgentEvalHarness", lambda: pytest.fail("合成用例不得启动调研图"))
    with pytest.raises(ValueError, match="固定合成证据"):
        runner.run_agent_evaluations([fixed], reports_dir=tmp_path)


def test_case_step_projection_exposes_contract_without_free_text(tmp_path):
    """验证用例步骤投影暴露契约字段且不含自由文本。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    sample = case("C1", tool_expectations=[{"tool_name": "search_web", "parameters": {"query": {"contains": "合成"}}}])
    raw = observation("C1")
    raw["tool_calls"][0]["parameters"]["内部联系方式"] = "不可发布"
    report = runner.run_agent_evaluations([sample], replay_file=recording(tmp_path, [raw]), reports_dir=tmp_path)
    row = report["case_results"][0]
    assert row["expected_tools"] == ["search_web"]
    assert row["expected_parameters"] == [{"tool_name": "search_web", "keys": ["query"]}]
    assert row["steps"] == [{"step": 1, "tool_name": "search_web", "status": "ok",
                             "dispatched": True, "data_count": 1, "parameter_keys": ["query"], "parameters_match": False}]
    assert row["steps_complete"] and row["status"] == "failed"
    serialized = json.dumps(report, ensure_ascii=False)
    assert all(private not in serialized for private in ("内部联系方式", "不可发布", "敏感查询原文"))


@pytest.mark.parametrize(("observed_zone", "matches"), [("America/New_York", True), ("Asia/Shanghai", False)])
def test_clock_parameter_matches_in_replay_and_published_steps(tmp_path, observed_zone, matches):
    """验证回放与发布步骤中的时区参数匹配结果一致。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
        observed_zone: ``observed_zone`` 参数化取值。
        matches: ``matches`` 参数化取值。
    """
    clock = "get_current_time"
    sample = case("CLOCK", required_tools=[clock], tool_expectations=[
        {"tool_name": clock, "parameters": {"timezone": "America/New_York"}, "status": "ok"}])
    raw = observation("CLOCK")
    raw["tool_calls"] = [{"tool_name": clock, "parameters": {"timezone": observed_zone}, "status": "ok",
                          "dispatched": True, "data_count": 1, "sequence": 1}]
    report = runner.run_agent_evaluations([sample], replay_file=recording(tmp_path, [raw]), reports_dir=tmp_path)
    _, rows = project_report(report)
    step, = rows[0]["observed"]
    assert step["tool_name"] == clock and step["parameter_keys"] == ["timezone"]
    assert step["parameters_match"] is matches and report["gate"]["passed"] is matches
    assert report["case_results"][0]["tool_calls_count"] == 1


def test_standard_regression_independently_blocks_and_challenge_is_separate(tmp_path):
    """验证标准集回归独立阻断，挑战集单独统计。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    cases = [case(f"C{i}") for i in range(1, 6)] + [case("C6", suite="challenge")]
    base_observations = [observation(c["id"]) for c in cases]
    path = recording(tmp_path, base_observations)
    runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    baseline_path = next((tmp_path / "synthetic_trace_regrade").glob("agent_eval_report_*.json"))
    path = recording(tmp_path, [
        {**o, "report": ""} if o["case_id"] in {"C1", "C6"} else o for o in base_observations
    ])
    report = runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path, compare_file=baseline_path)
    assert report["gate"]["benchmark_pass_rate"] == 0.8
    assert report["gate"]["passed"] is False
    assert report["gate"]["blocking_case_ids"] == ["C1"]
    comparison = report["comparison"]
    assert comparison["paired_counts"] == {"standard": 5, "challenge": 1}
    assert comparison["changes"]["standard"]["regressed"] == ["C1"]
    assert comparison["changes"]["challenge"]["regressed"] == ["C6"]


def test_pairing_excludes_definitions_missing_observations_and_different_runs(tmp_path):
    """验证配对排除缺观测、新定义与不同批次。

    Args:
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    cases = [case("C1"), case("C2"), case("C3"), case("C4")]
    path = recording(tmp_path, [observation("C1"), {**observation("C2"), "report": ""}, observation("C4")])
    baseline = runner.run_agent_evaluations(cases, replay_file=path, reports_dir=tmp_path)
    next_cases = [case("C1", keyword="changed"), case("C2"), case("C3"), case("C5")]
    path = recording(tmp_path, [observation(c["id"]) for c in next_cases])
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    current = runner.run_agent_evaluations(next_cases, replay_file=path, reports_dir=tmp_path, compare_file=baseline_path)
    delta = current["comparison"]
    assert delta["passed"]
    assert delta["changes"]["standard"]["fixed"] == ["C2"]
    assert delta["excluded"] == {"new": ["C5"], "baseline_only": ["C4"],
                                 "definition_changed": ["C1"], "unassessable": ["C3"]}
    assert delta["excluded_count"] == 4
    no_shared = {**baseline, "case_results": [baseline["case_results"][0]]}
    baseline_path.write_text(json.dumps(no_shared))
    result = runner.run_agent_evaluations(next_cases, replay_file=path, reports_dir=tmp_path, compare_file=baseline_path)
    assert not result["comparison"]["passed"]
    for field, new_value in (("mode", "live"), ("judges_sha256", "changed"), ("schema_version", 0)):
        baseline_path.write_text(json.dumps({**baseline, field: new_value}))
        with pytest.raises(ValueError):
            runner.run_agent_evaluations(next_cases, replay_file=path, reports_dir=tmp_path, compare_file=baseline_path)
