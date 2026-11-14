"""市场调研多智能体领域护栏、ToolOutcome、并行扇出与 ReAct 自纠错测试。"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from taozhi.core.tools.outcome import execute_tool_with_outcome
from taozhi.domain.errors import Denied
from taozhi.domain.research import (
    ResearchBudget,
    StopReason,
    SubagentTaskSpec,
    authorize_subagent_tasks,
    classify_research_scope,
    classify_steering_impact,
    enforce_tool_allowed,
    evaluate_hard_stop,
)
from taozhi.orchestration.agent_system import (
    _OrchestratorDecisionSchema,
    _ProposedSubtaskSchema,
    _SubagentStepSchema,
    execute_autonomous_agents,
)


def test_research_scope_and_task_tool_guard():
    """纯全网口碑调研封禁 1688/Amazon；且子智能体不得调用未被授权的工具。"""
    scope_web_only = classify_research_scope("检索全网关于【骨传导运动耳机】的最新行业趋势与买家差评")
    assert not scope_web_only.needs_sourcing
    assert "search_1688_wholesale" in scope_web_only.forbidden_tools
    assert "search_amazon" in scope_web_only.forbidden_tools

    enforce_tool_allowed("search_web", scope_web_only, task_tools=("search_web",))
    with pytest.raises(Denied) as exc_scope:
        enforce_tool_allowed("search_1688_wholesale", scope_web_only)
    assert exc_scope.value.status == 403
    assert exc_scope.value.code == "TOOL_OUT_OF_SCOPE"

    # 即使全局放行，若未授予当前角色也必须拦截
    scope_sourcing = classify_research_scope("对比【磁吸手机壳】1688 出厂底价与亚马逊售价")
    with pytest.raises(Denied) as exc_role:
        enforce_tool_allowed("search_amazon", scope_sourcing, task_tools=("search_1688_wholesale",))
    assert exc_role.value.status == 403
    assert exc_role.value.code == "TOOL_NOT_GRANTED"


def test_authorize_subagent_tasks_prunes_and_caps():
    """领域守卫必须裁掉越权任务与工具，并严格限制单轮并行数。"""
    scope_web_only = classify_research_scope("分析【智能保温杯】全网消费者痛点")
    budget = ResearchBudget(max_turns=5, max_parallel_subagents=2, max_subagent_steps=3, timeout_seconds=180.0)

    proposed = [
        SubagentTaskSpec("t1", "全网口碑员", "查口碑", ("search_web", "search_1688_wholesale"), ("智能保温杯",)),
        SubagentTaskSpec("t2", "越权采购员", "查1688底价", ("search_1688_wholesale", "search_amazon"), ("智能保温杯",)),
        SubagentTaskSpec("t3", "网页深抓员", "深抓评测", ("fetch_web_page",), ("https://example.com",)),
        SubagentTaskSpec("t4", "超额任务", "超出并发上限", ("search_web",), ("保温杯",)),
    ]

    plan = authorize_subagent_tasks(proposed, scope=scope_web_only, budget=budget)
    assert len(plan.tasks) == 2
    assert plan.tasks[0].allowed_tools == ("search_web",)
    assert plan.tasks[1].task_id == "t3"
    assert any("整单驳回" in msg for msg in plan.rejected_reasons)
    assert any("超出单轮并发上限" in msg for msg in plan.rejected_reasons)


def test_hard_stop_priorities():
    """校验硬停止优先级：CANCELLED > COMPLETED > TIMEOUT > MAX_TURNS > CONTINUE。"""
    budget = ResearchBudget(max_turns=5, max_parallel_subagents=4, max_subagent_steps=3, timeout_seconds=120.0)

    d_cancel = evaluate_hard_stop(turn=1, elapsed_seconds=1.0, cancelled=True, has_report=False, evidence_count=2, budget=budget)
    assert d_cancel.should_stop and not d_cancel.force_synthesize
    assert d_cancel.stop_reason == StopReason.CANCELLED

    d_done = evaluate_hard_stop(turn=2, elapsed_seconds=10.0, cancelled=False, has_report=True, evidence_count=2, budget=budget)
    assert d_done.should_stop and d_done.stop_reason == StopReason.COMPLETED

    d_timeout_salvage = evaluate_hard_stop(turn=2, elapsed_seconds=125.0, cancelled=False, has_report=False, evidence_count=2, budget=budget)
    assert d_timeout_salvage.should_stop and d_timeout_salvage.force_synthesize
    assert d_timeout_salvage.stop_reason == StopReason.TIMEOUT

    d_timeout_empty = evaluate_hard_stop(turn=2, elapsed_seconds=125.0, cancelled=False, has_report=False, evidence_count=0, budget=budget)
    assert d_timeout_empty.should_stop and not d_timeout_empty.force_synthesize

    d_max_turns = evaluate_hard_stop(turn=5, elapsed_seconds=20.0, cancelled=False, has_report=False, evidence_count=1, budget=budget)
    assert d_max_turns.should_stop and d_max_turns.force_synthesize
    assert d_max_turns.stop_reason == StopReason.MAX_TURNS

    d_cont = evaluate_hard_stop(turn=2, elapsed_seconds=20.0, cancelled=False, has_report=False, evidence_count=1, budget=budget)
    assert not d_cont.should_stop and d_cont.stop_reason == StopReason.CONTINUE


def test_tool_outcome_distinguishes_empty_error_timeout_cancel():
    """工具结构化回执必须显式区分 empty / error / timeout / cancelled。"""
    assert execute_tool_with_outcome("search_web", lambda query: [], {"query": "x"}).status == "empty"

    def boom(**_):
        raise RuntimeError("upstream 502")

    err = execute_tool_with_outcome("search_web", boom, {"query": "x"})
    assert err.status == "error"
    assert "upstream 502" in err.error_reason

    def slow(**_):
        time.sleep(1.5)
        return []

    to = execute_tool_with_outcome("search_web", slow, {"query": "x"}, remaining_seconds=0.5)
    assert to.status == "timeout"

    ev = threading.Event()
    ev.set()
    ca = execute_tool_with_outcome("search_web", lambda query: [{"title": "a"}], {"query": "x"}, cancel_event=ev)
    assert ca.status == "cancelled"


def test_steering_impact_classification():
    """验证运行期动态干预影响维度识别。"""
    imp_sourcing = classify_steering_impact("优先对比义乌 1688 源头工厂出厂报价")
    assert imp_sourcing.invalidate_wholesale and imp_sourcing.invalidate_verification and not imp_sourcing.invalidate_web

    imp_web = classify_steering_impact("重点补充全网消费者差评和痛点评价")
    assert imp_web.invalidate_web and imp_web.invalidate_verification


def test_parallel_orchestrator_and_react_query_refinement():
    """测试真多智能体并行调度 + 子智能体 ReAct 空结果自主换词重查 + 单调进度推进。"""
    events = []

    def emit(stage: str, pct: int):
        events.append((stage, pct))

    web_queries_seen = []

    def fake_search_web(query: str, limit: int = 15, timeout: float = 12.0):
        web_queries_seen.append(query)
        # 第一次窄词返回空，检验 ReAct 子智能体自己观察到 empty 后自主换词再搜
        if len(web_queries_seen) == 1:
            return []
        return [{"title": "便携充气泵实测", "snippet": "散热噪音是主要差评点，主流零售 129 元"}]

    lock = threading.Lock()
    state_counters = {"orch": 0, "web_steps": 0, "sourcing_steps": 0}

    def fake_chat_json(*, prompt: str, schema=None, **kwargs):
        if schema is _OrchestratorDecisionSchema:
            with lock:
                state_counters["orch"] += 1
                n = state_counters["orch"]
            if n == 1:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="同时启动全网差评深挖与1688出厂价核查两路并行调查",
                    outline=["市场需求与痛点", "供应链成本结构", "选品策略"],
                    subtasks=[
                        _ProposedSubtaskSchema(
                            role="全网差评深挖专员",
                            goal="查明便携充气泵消费者痛点与零售定价",
                            allowed_tools=["search_web", "fetch_web_page"],
                            seed_queries=["便携充气泵 极致窄词 差评"],
                            channel_hint="web",
                        ),
                        _ProposedSubtaskSchema(
                            role="1688源头工厂采购专员",
                            goal="核查1688便携充气泵出厂底价与MOQ",
                            allowed_tools=["search_1688_wholesale"],
                            seed_queries=["便携充气泵"],
                            channel_hint="sourcing",
                        ),
                    ],
                )
            return _OrchestratorDecisionSchema(
                action="finish",
                thought="双路证据包均已齐备且数据有效，移交合成研报",
                outline=["市场需求与痛点", "供应链成本结构", "选品策略"],
            )

        # Subagent ReAct 步骤模拟
        if "全网差评深挖专员" in prompt:
            with lock:
                state_counters["web_steps"] += 1
                step = state_counters["web_steps"]
            if step == 1:
                return _SubagentStepSchema(action="call_tool", thought="首轮宽窄词测试检索", tool_name="search_web", query_or_url="便携充气泵 极致窄词")
            if step == 2:
                return _SubagentStepSchema(action="call_tool", thought="上轮返回empty，自主放宽关键词重查", tool_name="search_web", query_or_url="便携充气泵 差评")
            return _SubagentStepSchema(
                action="finish",
                thought="已拿到痛点与零售价实证",
                findings_summary="消费者高频痛点为散热噪音，主流零售价约 129 元。",
                key_metrics=["零售价约129元"],
            )

        with lock:
            state_counters["sourcing_steps"] += 1
            s_step = state_counters["sourcing_steps"]
        if s_step == 1:
            return _SubagentStepSchema(action="call_tool", thought="检索1688出厂价", tool_name="search_1688_wholesale", query_or_url="便携充气泵")
        return _SubagentStepSchema(
            action="finish",
            thought="已获1688源头底价",
            findings_summary="1688源头工厂出厂价 38 元，起订量 50 个。",
            key_metrics=["1688出厂价38元", "MOQ 50个"],
        )

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = fake_chat_json
    mock_llm.chat.return_value = "# 便携充气泵市场调研报告\n\n## 1. 市场需求与痛点\n散热噪音突出，零售 129 元；1688 出厂 38 元。"

    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        with patch("taozhi.orchestration.agent_system.search_web", side_effect=fake_search_web):
            with patch("taozhi.orchestration.agent_system.search_1688_wholesale", return_value=[{"title": "东莞充气泵源头厂", "price": 38}]):
                res = execute_autonomous_agents(
                    keyword="便携充气泵",
                    user_query="调研便携充气泵全网差评与1688出厂价",
                    emit_event_fn=emit,
                )

    assert res["system_status"] == "COMPLETED"
    assert len(web_queries_seen) == 2  # 验证 Subagent 观察到 empty 后自主换词重查
    assert len(res["evidence_pool"]) == 2
    assert len(res["platforms"]) == 2
    pcts = [pct for _, pct in events]
    assert pcts == sorted(pcts)  # 进度必须严格单调不回退
    assert pcts[-1] == 100


def test_live_cancel_and_mid_flight_steering():
    """测试运行中取消熔断，以及中途动态 Steering 回调驱动主脑增量补采。"""
    # A. 实时取消
    mock_llm_cancel = MagicMock()
    mock_llm_cancel.chat_json.side_effect = [
        _OrchestratorDecisionSchema(
            action="spawn_subagents",
            thought="首轮调研",
            subtasks=[_ProposedSubtaskSchema(role="全网专员", goal="查趋势", allowed_tools=["search_web"], seed_queries=["无线耳机"])],
        ),
        _SubagentStepSchema(action="call_tool", thought="搜全网", tool_name="search_web", query_or_url="无线耳机"),
        _SubagentStepSchema(action="finish", thought="完成", findings_summary="全网销量高。"),
    ]
    cancel_checks = iter([False, True])
    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm_cancel):
        with patch("taozhi.orchestration.agent_system.search_web", return_value=[{"title": "耳机", "snippet": "热销"}]):
            res_cancel = execute_autonomous_agents(
                keyword="无线耳机",
                check_cancel_fn=lambda: next(cancel_checks, True),
            )
    assert res_cancel["cancelled"] is True
    assert res_cancel["stop_reason"] == StopReason.CANCELLED.value
    assert res_cancel["report"] == ""

    # B. 中途动态 Steering 触发补派
    steer_calls = {"n": 0}

    def fetch_steering():
        steer_calls["n"] += 1
        return [] if steer_calls["n"] == 1 else ["补充核查 1688 源头工厂出厂价"]

    events = []
    mock_llm_steer = MagicMock()

    def steer_chat_json(*, prompt: str, schema=None, **kwargs):
        if schema is _OrchestratorDecisionSchema:
            if "补充核查 1688" not in prompt:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="先查全网口碑",
                    subtasks=[_ProposedSubtaskSchema(role="全网专员", goal="查口碑", allowed_tools=["search_web"], seed_queries=["无线鼠标"])],
                )
            if "1688采购专员" not in prompt:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="收到用户中途干预，增派1688采购专员核查出厂底价",
                    subtasks=[_ProposedSubtaskSchema(role="1688采购专员", goal="查1688底价", allowed_tools=["search_1688_wholesale"], seed_queries=["无线鼠标"])],
                )
            return _OrchestratorDecisionSchema(action="finish", thought="证据与干预均已落地，合成研报")

        if "1688" in prompt:
            return _SubagentStepSchema(action="finish", thought="完成1688", findings_summary="1688出厂价18元。")
        return _SubagentStepSchema(action="finish", thought="完成全网", findings_summary="全网口碑良好。")

    mock_llm_steer.chat_json.side_effect = steer_chat_json
    mock_llm_steer.chat.return_value = "# 无线鼠标研报\n已按动态干预补充核查 1688 出厂底价 18 元。"

    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm_steer):
        res_steer = execute_autonomous_agents(
            keyword="无线鼠标",
            emit_event_fn=lambda s, p: events.append((s, p)),
            fetch_steering_fn=fetch_steering,
        )

    assert res_steer["cancelled"] is False
    assert res_steer["system_status"] == "COMPLETED"
    assert any(s.startswith("[steering]") for s, _ in events)
    roles = [s["role"] for s in res_steer["subagents"]]
    assert "1688采购专员" in roles
