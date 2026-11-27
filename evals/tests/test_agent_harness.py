"""Agent 执行采集、运行中干预与离线记录重评分的契约。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from evals.agent_eval.harness import AgentEvalHarness, replay_transcript


CASE = {"id": "SYN-OBS", "prompt": "查询合成型号", "steering": ["只查新型号"]}
TRANSCRIPT = {"case_id": CASE["id"], "system_status": "COMPLETED", "report": "合成回答", "tool_calls": []}


def test_live_observations_distinguish_tool_outcomes_and_missing_usage():
    """验证实时观测区分工具执行结果并保留缺失的用量信息。"""
    def run(**hooks):
        """把注入钩子交给评测装置执行并返回运行结果。

        Args:
            hooks: 测试注入的回调集合。
        """
        hooks["observe_trace"]("trace1")
        observe = hooks["model_call_fn"]
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda event: observe("research", event), [
                {"prompt_tokens": 12, "completion_tokens": 3, "model": "synthetic-eval"},
                {"outcome": "failed"},
                {"prompt_tokens": 0, "completion_tokens": 0},
            ]))
        assert hooks["fetch_steering_fn"]() == []
        emit = hooks["emit_event_fn"]
        emit("[tool_execution_end] [市场情报分析师] get_current_time 完成", 30)
        assert hooks["fetch_steering_fn"]() == []
        emit("[tool_execution_end] [1688 采购分析师] search_1688_wholesale 采集完成", 35)
        assert hooks["fetch_steering_fn"]() == ["只查新型号"]
        assert hooks["fetch_steering_fn"]() == []
        emit("[steering] 已接收 1 条补充干预指令", 40)
        return {
            "report": "合成回答", "system_status": "COMPLETED", "model_calls": 3,
            "subagents": [{"role": "sourcing", "task_id": "task1", "agent_type": "subagent", "tools": [
                {"name": "search_1688_wholesale", "status": "empty", "dispatched": True, "parameters": {"keyword": "新型号"}, "data_count": 0},
                {"name": "search_amazon", "status": "error", "dispatched": False, "parameters": {"query_or_url": "新型号"}, "data_count": 0},
                {"name": "unknown"},
            ]}],
            "evidence_pool": [
                {"evidence_id": "E1", "status": "ok", "platform_items": {"web": [{"url": "https://a.example/"}]}},
                {"evidence_id": "UNUSABLE", "status": "error"},
            ],
        }
    result = AgentEvalHarness(execute=run).run(CASE)
    assert result.subagents_count == 1
    assert result.trace_id == "trace1"
    assert result.steering_applied
    assert result.evidence_ids == ["E1", "https://a.example/"]
    assert result.model_names == ["synthetic-eval"]
    assert [tool["dispatched"] for tool in result.tool_calls] == [True, False, None]
    assert [tool["status"] for tool in result.tool_calls] == ["empty", "error", None]
    assert [tool["sequence"] for tool in result.tool_calls] == [1, 2, 3]
    assert result.token_usage == {"model_calls": 3, "measured_calls": 1, "prompt_tokens": 12, "completion_tokens": 3}


def test_parallel_workers_do_not_invent_global_order_or_acknowledgement():
    """验证并行工作者不臆造全局顺序或干预确认，未知项如实标记。"""
    def run(**hooks):
        """构造并返回含并行子智能体的执行结果。

        Args:
            hooks: 测试注入的回调集合。
        """
        hooks["emit_event_fn"]("[tool_execution_end] [市场情报分析师] search_web 采集完成", 40)
        assert hooks["fetch_steering_fn"]() == CASE["steering"]
        return {"subagents": [
            {"tools": [{"name": "fetch_web_page", "parameters": {"url": "https://a.example/"}}]},
            {"tools": [{"name": "search_web", "parameters": {"query": "型号"}}]},
        ]}
    result = AgentEvalHarness(execute=run).run(CASE)
    assert [item["sequence"] for item in result.tool_calls] == [None, None]
    assert not result.steering_applied
    assert result.system_status == "UNKNOWN"
    assert result.evidence_ids is None
    assert result.token_usage["prompt_tokens"] is None


def test_failed_graph_retains_observed_usage_without_exception_body():
    """验证图执行失败时保留已观测用量且不泄漏异常正文。"""
    def run(**hooks):
        """记录已观测用量后抛出超时异常，模拟图执行失败。

        Args:
            hooks: 测试注入的回调集合。
        """
        hooks["model_call_fn"]("agent", {"prompt_tokens": 7, "completion_tokens": 2})
        raise TimeoutError("上游返回的业务正文")
    result = AgentEvalHarness(execute=run).run(CASE)
    assert result.system_status == "FAILED"
    assert result.error == "TimeoutError"
    assert result.token_usage["prompt_tokens"] == 7
    assert not result.tool_trace_complete


def test_missing_tool_collection_differs_from_an_observed_empty_trace():
    """验证未采集工具轨迹与观测到空轨迹可以区分。"""
    unobserved = AgentEvalHarness(execute=lambda **kwargs: {"report": "回答"}).run(CASE)
    assert not unobserved.tool_trace_complete
    observed = AgentEvalHarness(execute=lambda **kwargs: {"subagents": []}).run(CASE)
    assert observed.tool_trace_complete
    assert not replay_transcript(CASE, {**TRANSCRIPT, "tool_trace_complete": False}).tool_trace_complete


def test_transcript_is_explicit_and_preserves_unknown_fields():
    """验证回放记录显式声明字段并把未知项保留为未知。"""
    run = replay_transcript(CASE, TRANSCRIPT)
    assert run.subagents_count is None
    assert run.report == TRANSCRIPT["report"]
    assert run.elapsed is None and run.evidence_ids is None
    assert run.token_usage["model_calls"] is None
    assert run.token_usage["prompt_tokens"] is None
    known = replay_transcript(CASE, {
        **TRANSCRIPT, "elapsed": 1.5, "evidence_ids": [], "token_usage": {
            "model_calls": 1, "measured_calls": 1, "prompt_tokens": 8, "completion_tokens": 0,
        },
    })
    assert known.elapsed == 1.5 and known.evidence_ids == []
    assert known.token_usage["completion_tokens"] == 0


@pytest.mark.parametrize("overrides", [
    {"case_id": "OTHER"}, {"system_status": ""}, {"report": None}, {"tool_calls": None},
    {"tool_calls": [{"tool_name": "search_web", "dispatched": "false"}]},
    {"tool_calls": [{"tool_name": "search_web", "status": "success"}]},
    {"tool_calls": [{"tool_name": "t", "sequence": 1}, {"tool_name": "t", "sequence": 1}]},
    {"tool_calls": [{"tool_name": "t", "data_count": True}]},
    {"elapsed": -2}, {"elapsed": float("nan")}, {"evidence_ids": "E1"},
    {"token_usage": {"prompt_tokens": 0, "measured_calls": 0}},
    {"token_usage": {"model_calls": 1, "measured_calls": 2}},
    {"token_usage": {"model_calls": "1"}}, {"steering_applied": "false"},
])
def test_malformed_transcript_fails_before_scoring(overrides):
    """验证字段缺失或类型错误的回放记录在评分前即失败。

    Args:
        overrides: ``overrides`` 参数化取值。
    """
    with pytest.raises(ValueError):
        replay_transcript(CASE, {**TRANSCRIPT, **overrides})
