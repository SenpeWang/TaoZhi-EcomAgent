"""多智能体可观测评测体系单元测试（移植自 Piagent 测试规范）。"""
from __future__ import annotations

from unittest.mock import patch
import pytest

from evals.agent_eval.harness import AgentEvalHarness, AgentRunResult
from evals.agent_eval.judges import (
    AntiHallucinationJudge,
    DeliverableJudge,
    SteeringJudge,
    ToolExecutionJudge,
)
from evals.agent_eval.cases import AGENT_BENCHMARK_CASES


def test_tool_execution_judge_success():
    judge = ToolExecutionJudge(required_tools=["search_1688_wholesale", "search_amazon"], forbidden_tools=["dangerous_tool"])
    run_result = AgentRunResult(
        case_id="C01",
        task_prompt="test",
        keyword="保温杯",
        report="报告正文",
        subagents=[],
        tool_calls=[
            {"tool_name": "search_1688_wholesale", "data_count": 10},
            {"tool_name": "search_amazon", "data_count": 5},
        ],
        state_transitions=[],
        artifacts=[],
        elapsed=1.2,
        trace_id="abc123",
        system_status="COMPLETED",
    )
    verdict = judge.evaluate(run_result)
    assert verdict.passed is True
    assert verdict.score == 1.0


def test_tool_execution_judge_forbidden_and_missing():
    judge = ToolExecutionJudge(required_tools=["search_1688_wholesale"], forbidden_tools=["search_amazon"])
    # 违规调用了 search_amazon
    bad_result = AgentRunResult(
        case_id="C02",
        task_prompt="test",
        keyword="耳机",
        report="报告正文",
        subagents=[],
        tool_calls=[
            {"tool_name": "search_amazon", "data_count": 5},
        ],
        state_transitions=[],
        artifacts=[],
        elapsed=1.0,
        trace_id="abc123",
        system_status="COMPLETED",
    )
    verdict = judge.evaluate(bad_result)
    assert verdict.passed is False
    assert "受限工具" in verdict.reason
    assert "缺少必须调用" in verdict.reason


def test_deliverable_judge():
    judge = DeliverableJudge(required_sections=["市场", "供应链", "建议"], min_chars=50)

    # 合规报告
    good_report = "# 市场全景调研\n## 1. 市场概况\n需求旺盛...\n## 2. 供应链出厂价\n底价8元...\n## 3. 操盘建议\n打造差异化差异点。"
    res_good = AgentRunResult(
        case_id="C03",
        task_prompt="test",
        keyword="手机壳",
        report=good_report,
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=2.0,
        trace_id="t1",
        system_status="COMPLETED",
    )
    assert judge.evaluate(res_good).passed is True

    # 缺失供应链小节
    short_report = "# 市场概述\n内容太短"
    res_bad = AgentRunResult(
        case_id="C04",
        task_prompt="test",
        keyword="手机壳",
        report=short_report,
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=0.5,
        trace_id="t2",
        system_status="COMPLETED",
    )
    verdict_bad = judge.evaluate(res_bad)
    assert verdict_bad.passed is False
    assert "字符数不足" in verdict_bad.reason or "缺失关键大纲" in verdict_bad.reason


def test_anti_hallucination_judge():
    judge = AntiHallucinationJudge()

    # 包含假日志模板 ➔ 判定失败
    fake_res = AgentRunResult(
        case_id="C05",
        task_prompt="test",
        keyword="保温杯",
        report="思考\n总指挥 正在规划...",
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=1.0,
        trace_id="t3",
        system_status="COMPLETED",
    )
    assert judge.evaluate(fake_res).passed is False
    assert "虚假思考文案" in judge.evaluate(fake_res).reason

    # 干净报告 ➔ 判定通过
    clean_res = AgentRunResult(
        case_id="C06",
        task_prompt="test",
        keyword="保温杯",
        report="经实测调研，源头工厂底价为 6.8 元。",
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=1.0,
        trace_id="t4",
        system_status="COMPLETED",
    )
    assert judge.evaluate(clean_res).passed is True


def test_steering_judge():
    judge = SteeringJudge(steering_keyword="义乌")

    # 遵循干预
    res_steered = AgentRunResult(
        case_id="C07",
        task_prompt="test",
        keyword="充电宝",
        report="已根据用户指令，重点对比了义乌源头工厂的报价。",
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=1.5,
        trace_id="t5",
        system_status="COMPLETED",
        steering_applied=True,
    )
    assert judge.evaluate(res_steered).passed is True

    # 遗漏干预
    res_missed = AgentRunResult(
        case_id="C08",
        task_prompt="test",
        keyword="充电宝",
        report="全国各省市均有供应商分布。",
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=1.5,
        trace_id="t6",
        system_status="COMPLETED",
        steering_applied=True,
    )
    assert judge.evaluate(res_missed).passed is False


def test_harness_mock_run():
    harness = AgentEvalHarness()
    mock_raw = {
        "report": "# 智能保温杯研报\n## 1. 市场概况\n## 2. 1688货源\n## 3. 操盘建议",
        "subagents": [
            {
                "role": "全网市场情报分析专员",
                "type_name": "web_agent",
                "tools": [{"name": "search_web", "description": "抓取15条", "data_count": 15}],
            }
        ],
        "artifacts": ["market_research_report.md"],
        "audit_trail": [],
        "system_status": "COMPLETED",
    }
    with patch("evals.agent_eval.harness.execute_autonomous_agents", return_value=mock_raw):
        case = AGENT_BENCHMARK_CASES[0]
        res = harness.run(case)
        assert res.case_id == case["id"]
        assert len(res.tool_calls) == 1
        assert res.tool_calls[0]["tool_name"] == "search_web"
        assert res.system_status == "COMPLETED"

def test_error_recovery_case():
    err_case = next(c for c in AGENT_BENCHMARK_CASES if c["id"] == "AGENT-ERR-005")
    assert err_case["category"] == "error_recovery"
    assert "search_web" in err_case["required_tools"]
    judge = DeliverableJudge(required_sections=err_case["required_sections"], min_chars=err_case["min_chars"])

    # 模拟降级保底报告（满足 min_chars 60 与关键大纲要求）
    fallback_report = (
        "# 市场概况与供应链异常降级分析报告\n"
        "经多方信源探查，该特种高精密陶瓷轴承在公开批发市场货源极度稀缺。\n"
        "针对异常情况启动错误恢复降级策略，建议企业转向特种工业定制与打样开模。"
    )
    res = AgentRunResult(
        case_id=err_case["id"],
        task_prompt=err_case["prompt"],
        keyword=err_case["keyword"],
        report=fallback_report,
        subagents=[],
        tool_calls=[],
        state_transitions=[],
        artifacts=[],
        elapsed=1.0,
        trace_id="t_err",
        system_status="COMPLETED",
    )
    assert judge.evaluate(res).passed is True
