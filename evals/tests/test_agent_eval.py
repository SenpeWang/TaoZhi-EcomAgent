"""多智能体可观测评测体系单元测试（移植自 Piagent 测试规范）。"""
from __future__ import annotations

from evals.agent_eval.harness import AgentRunResult
from evals.agent_eval.judges import (
    AntiHallucinationJudge,
    DeliverableJudge,
    SteeringJudge,
    evaluate_case,
)
from evals.agent_eval.cases import AGENT_BENCHMARK_CASES


def test_forbidden_dispatch_and_missing_source_are_reported_independently():
    result = AgentRunResult(
        report="概况 供应链 建议", system_status="COMPLETED",
        tool_calls=[{"tool_name": "search_amazon", "dispatched": True, "data_count": 5}],
    )
    checks = evaluate_case({"required_tools": ["search_1688_wholesale"], "forbidden_tools": ["search_amazon"]}, result)
    assert {check.failure_type for check in checks} >= {"tool_forbidden", "tool_missing"}


def test_deliverable_judge():
    judge = DeliverableJudge(required_sections=["市场", "供应链", "建议"], min_chars=50)

    good_report = "# 市场全景调研\n## 1. 市场概况\n需求旺盛...\n## 2. 供应链出厂价\n底价8元...\n## 3. 操盘建议\n打造差异化差异点。"
    res_good = AgentRunResult(
        report=good_report,
        system_status="COMPLETED",
    )
    assert judge.evaluate(res_good).passed is True

    short_report = "# 市场概述\n内容太短"
    res_bad = AgentRunResult(
        report=short_report,
        system_status="COMPLETED",
    )
    verdict_bad = judge.evaluate(res_bad)
    assert verdict_bad.passed is False
    assert "字符数不足" in verdict_bad.reason or "缺失关键大纲" in verdict_bad.reason


def test_anti_hallucination_judge():
    judge = AntiHallucinationJudge()

    fake_res = AgentRunResult(
        report="思考\n总指挥 正在规划...",
        system_status="COMPLETED",
    )
    assert judge.evaluate(fake_res).passed is False
    assert "虚假思考文案" in judge.evaluate(fake_res).reason

    clean_res = AgentRunResult(
        report="经实测调研，源头工厂底价为 6.8 元。",
        system_status="COMPLETED",
    )
    assert judge.evaluate(clean_res).passed is True


def test_steering_judge():
    judge = SteeringJudge(steering_keyword="义乌")

    res_steered = AgentRunResult(
        report="已根据用户指令，重点对比了义乌源头工厂的报价。",
        system_status="COMPLETED",
        steering_applied=True,
    )
    assert judge.evaluate(res_steered).passed is True

    res_missed = AgentRunResult(
        report="全国各省市均有供应商分布。",
        system_status="COMPLETED",
        steering_applied=True,
    )
    assert judge.evaluate(res_missed).passed is False


def test_error_recovery_case():
    err_case = next(c for c in AGENT_BENCHMARK_CASES if c["id"] == "AGENT-ERR-005")
    assert err_case["category"] == "error_recovery"
    assert "search_web" in err_case["required_tools"]
    judge = DeliverableJudge(required_sections=err_case["required_sections"], min_chars=err_case["min_chars"])

    fallback_report = (
        "# 市场概况与供应链异常降级分析报告\n"
        "经多方信源探查，该特种高精密陶瓷轴承在公开批发市场货源极度稀缺。\n"
        "针对异常情况启动错误恢复降级策略，建议企业转向特种工业定制与打样开模。"
    )
    res = AgentRunResult(
        report=fallback_report,
        system_status="COMPLETED",
    )
    assert judge.evaluate(res).passed is True
