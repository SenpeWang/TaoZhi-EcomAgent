"""市场调研领域规则、停止条件、边界硬守卫与流式干预单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from taozhi.domain.errors import Denied
from taozhi.domain.research import (
    ResearchSnapshot,
    StopReason,
    classify_research_scope,
    classify_steering_impact,
    enforce_tool_allowed,
    evaluate_next_step,
)
from taozhi.orchestration.agent_system import execute_autonomous_agents


def test_research_scope_boundary_guard():
    """纯全网口碑调研必须封禁 1688 与 Amazon 工具，防止越权调用。"""
    scope_web_only = classify_research_scope("检索全网关于【骨传导运动耳机】的最新行业趋势与买家差评")
    assert not scope_web_only.needs_sourcing
    assert "search_1688_wholesale" in scope_web_only.forbidden_tools
    assert "search_amazon" in scope_web_only.forbidden_tools

    # 领域硬守卫放行 search_web，拦截受限工具
    enforce_tool_allowed("search_web", scope_web_only)
    with pytest.raises(Denied) as exc_info:
        enforce_tool_allowed("search_1688_wholesale", scope_web_only)
    assert exc_info.value.status == 403

    # 含供应链关键词时放行双渠道工具
    scope_sourcing = classify_research_scope("对比【磁吸手机壳】1688 出厂底价与亚马逊售价")
    assert scope_sourcing.needs_sourcing
    assert scope_sourcing.forbidden_tools == ()
    enforce_tool_allowed("search_1688_wholesale", scope_sourcing)
    enforce_tool_allowed("search_amazon", scope_sourcing)


def test_stopping_policy_all_conditions():
    """校验取消、全局超时、轮次硬上限、空结果重抓与正常完成 5 类裁决。"""
    scope = classify_research_scope("调研【智能保温杯】1688 出厂价与全网痛点")

    # 1. 取消信号最高优先级熔断
    d_cancel = evaluate_next_step(
        ResearchSnapshot(
            turn=1,
            max_turns=6,
            elapsed_seconds=1.0,
            timeout_seconds=180.0,
            cancelled=True,
            scope=scope,
            has_web=False,
            web_count=0,
            has_wholesale=False,
            wholesale_count=0,
            has_verification=False,
            has_report=False,
        )
    )
    assert d_cancel.stop_reason == StopReason.CANCELLED
    assert d_cancel.next_action == "FINISH"

    # 2. 全局超时且已有部分事实时，紧急收敛出报告
    d_timeout_partial = evaluate_next_step(
        ResearchSnapshot(
            turn=2,
            max_turns=6,
            elapsed_seconds=185.0,
            timeout_seconds=180.0,
            cancelled=False,
            scope=scope,
            has_web=True,
            web_count=5,
            has_wholesale=False,
            wholesale_count=0,
            has_verification=False,
            has_report=False,
        )
    )
    assert d_timeout_partial.stop_reason == StopReason.TIMEOUT
    assert d_timeout_partial.next_action == "reporter_agent"

    # 3. 轮次达到 max_turns 硬上限，强制收敛
    d_max_turns = evaluate_next_step(
        ResearchSnapshot(
            turn=6,
            max_turns=6,
            elapsed_seconds=10.0,
            timeout_seconds=180.0,
            cancelled=False,
            scope=scope,
            has_web=True,
            web_count=5,
            has_wholesale=False,
            wholesale_count=0,
            has_verification=False,
            has_report=False,
        )
    )
    assert d_max_turns.stop_reason == StopReason.MAX_TURNS
    assert d_max_turns.next_action == "reporter_agent"

    # 4. 空采集且存在重试预算时，允许重新调度采集
    d_retry = evaluate_next_step(
        ResearchSnapshot(
            turn=2,
            max_turns=6,
            elapsed_seconds=5.0,
            timeout_seconds=180.0,
            cancelled=False,
            scope=scope,
            has_web=True,
            web_count=0,
            has_wholesale=True,
            wholesale_count=5,
            has_verification=False,
            has_report=False,
            web_retries=1,
            max_empty_retries=2,
        )
    )
    assert d_retry.stop_reason == StopReason.CONTINUE
    assert d_retry.next_action == "web_agent"

    # 5. 报告就绪 -> 正常完成
    d_done = evaluate_next_step(
        ResearchSnapshot(
            turn=4,
            max_turns=6,
            elapsed_seconds=20.0,
            timeout_seconds=180.0,
            cancelled=False,
            scope=scope,
            has_web=True,
            web_count=5,
            has_wholesale=True,
            wholesale_count=5,
            has_verification=True,
            has_report=True,
        )
    )
    assert d_done.stop_reason == StopReason.COMPLETED
    assert d_done.next_action == "FINISH"


def test_steering_impact_classification():
    """验证中途动态干预按指令影响范围重置对应事实池。"""
    imp_sourcing = classify_steering_impact("优先对比义乌 1688 源头工厂出厂报价")
    assert imp_sourcing.invalidate_wholesale
    assert imp_sourcing.invalidate_verification
    assert not imp_sourcing.invalidate_web

    imp_web = classify_steering_impact("重点补充全网消费者差评和痛点评价")
    assert imp_web.invalidate_web
    assert imp_web.invalidate_verification


def test_execute_autonomous_agents_live_cancel_and_steering():
    """测试运行期取消即时止损与增量干预回调真实生效。"""
    events = []

    def emit(stage: str, pct: int):
        events.append((stage, pct))

    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        '["市场概况", "竞品口碑"]',
        "全网采集摘要：聚焦轻量化设计。",
    ]

    # 第 1 轮返回未取消，第 2 轮（web_agent 返回主脑后）模拟用户取消
    cancel_checks = iter([False, True])
    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        with patch("taozhi.orchestration.agent_system.search_web", return_value=[{"title": "测评", "snippet": "好评"}]):
            res = execute_autonomous_agents(
                keyword="无线蓝牙耳机",
                emit_event_fn=emit,
                check_cancel_fn=lambda: next(cancel_checks, True),
            )

    assert res["cancelled"] is True
    assert res["system_status"] == "CANCELLED"
    assert res["stop_reason"] == StopReason.CANCELLED.value
    # 取消后直接熔断，不再调用 reporter_agent 写研报
    assert res["report"] == ""
    assert len(res["subagents"]) == 1
    assert any(s.startswith("[system]") for s, _ in events)


def test_execute_autonomous_agents_mid_flight_steering_callback():
    """测试通过 fetch_steering_fn 在第 2 轮注入供应链干预，动态唤醒 scanner_agent。"""
    events = []

    def emit(stage: str, pct: int):
        events.append((stage, pct))

    # 第 1 轮空干预；第 2 轮（web_agent 结束后）注入 1688 干预，触发扩编 scanner + verifier + reporter
    steer_calls = {"n": 0}

    def fetch_steering():
        steer_calls["n"] += 1
        return [] if steer_calls["n"] == 1 else ["追加调研 1688 源头工厂出厂价"]

    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        '["市场", "口碑"]',
        "全网摘要：热销价 99 元。",
        "1688 与亚马逊比价摘要：出厂价 18 元。",
        "验真摘要：毛利空间合理。",
        "# 无线鼠标全景调研报告\n已按干预重点核查 1688 出厂价。",
    ]

    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        with patch("taozhi.orchestration.agent_system.search_web", return_value=[{"title": "鼠标", "snippet": "热销"}]):
            with patch("taozhi.orchestration.agent_system.search_1688_wholesale", return_value=[{"title": "工厂", "price": 18}]):
                with patch("taozhi.orchestration.agent_system.search_amazon", return_value=[{"title": "Mouse", "price": "$29"}]):
                    res = execute_autonomous_agents(
                        keyword="无线鼠标",
                        emit_event_fn=emit,
                        fetch_steering_fn=fetch_steering,
                    )

    assert res["cancelled"] is False
    assert res["system_status"] == "COMPLETED"
    subagent_types = [s["type_name"] for s in res["subagents"]]
    assert "web_agent" in subagent_types
    assert "scanner_agent" in subagent_types
    assert "verifier_agent" in subagent_types
    assert "reporter_agent" in subagent_types
    assert any(s.startswith("[steering]") for s, _ in events)
