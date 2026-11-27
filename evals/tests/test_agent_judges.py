"""确定性验证离线回放的断言、缺测与严重失败语义。"""
from __future__ import annotations

import unittest

from evals.agent_eval.harness import AgentRunResult
from evals.agent_eval.judges import ReferenceFactJudge, ToolBoundaryJudge, ToolExecutionJudge, evaluate_case


def run_result(calls=(), report="", **fields):
    """构造带工具调用与报告文本的运行结果。

    Args:
        calls: 工具调用记录列表。
        report: 报告正文。
        fields: 需要覆盖的运行结果字段。
    """
    return AgentRunResult(report=report, tool_calls=list(calls), system_status=fields.pop("system_status", "COMPLETED"), **fields)


def call(name, **fields):
    """构造单条工具调用记录。

    Args:
        name: 工具名称。
        fields: 附加的调用字段。
    """
    return {"tool_name": name, "dispatched": True, **fields}


class ToolContractTests(unittest.TestCase):
    """工具执行契约判定的单元测试集合。"""

    def test_clock_reading_does_not_satisfy_market_data_minimum(self):
        """验证读取时钟不计入市场数据类工具的数据量下限。"""
        judge = ToolExecutionJudge(min_data_count=2)
        clock = call("get_current_time", status="ok", data_count=1)
        search = call("search_web", status="ok", data_count=1)
        self.assertEqual(judge.evaluate(run_result([clock, search])).failure_type, "tool_data")
        self.assertTrue(judge.evaluate(run_result([clock, {**search, "data_count": 2}])).passed)

    def test_each_required_source_must_provide_data(self):
        """验证每个必需来源工具都必须提供数据。"""
        judge = ToolExecutionJudge(required_tools=["search_web", "search_amazon"])
        res = run_result([call("search_web", status="ok", data_count=11),
                          call("search_amazon", status="empty", data_count=0)])
        self.assertEqual(judge.evaluate(res).failure_type, "tool_data")

    def test_status_and_arguments_are_checked_on_the_same_call(self):
        """验证状态与参数在同一调用上校验并统计命中数。"""
        judge = ToolExecutionJudge(tool_expectations=[{
            "tool_name": "search_web", "parameters": {"query": {"contains": "SYN-P8"}}, "status": "ok",
        }])
        wrong = call("search_web", status="ok", parameters={"query": "SYN-P9"})
        correct = call("search_web", status="timeout", parameters={"query": "查询 SYN-P8"})
        verdict = judge.evaluate(run_result([wrong, correct]))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.details["parameter_hits"], 1)
        self.assertEqual(verdict.details["parameter_total"], 2)
        self.assertTrue(judge.evaluate(run_result([{**correct, "status": "ok"}])).passed)

    def test_transient_request_then_success(self):
        """验证请求先失败后成功时仍判定通过。"""
        judge = ToolExecutionJudge(tool_expectations=[{
            "tool_name": "search_web", "parameters": {"query": "SYN"}, "status": "ok", "min_data_count": 1,
        }])
        args = {"query": "SYN"}
        verdict = judge.evaluate(run_result([
            call("search_web", parameters=args, status="timeout", data_count=0),
            call("search_web", parameters=args, status="ok", data_count=1),
        ]))
        self.assertTrue(verdict.passed)

    def test_forbidden_attempt_distinct_from_actual_execution(self):
        """验证未派发的禁用尝试与实际执行分别计数。"""
        judge = ToolBoundaryJudge(allowed_tools=["search_web"], forbidden_tools=[])
        blocked = call("search_amazon", dispatched=False, status="error", sequence=1)
        verdict = judge.evaluate(run_result([blocked]))
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.details["forbidden_attempts"], 1)
        self.assertEqual(verdict.details["forbidden_executions"], 0)
        sent = judge.evaluate(run_result([{**blocked, "dispatched": True}]))
        self.assertTrue(sent.hard_failure)
        self.assertEqual(sent.details["issues"][0]["step"], 1)

    def test_legal_empty_is_distinct_from_timeout_and_missing_capture(self):
        """验证合法空结果与超时、缺失采集可以区分。"""
        judge = ToolExecutionJudge(tool_expectations=[{"tool_name": "search_web", "status": "empty", "min_data_count": 0}])
        self.assertTrue(judge.evaluate(run_result([call("search_web", status="empty", data_count=0)])).passed)
        wrong = judge.evaluate(run_result([call("search_web", status="timeout", data_count=0)]))
        self.assertEqual(wrong.failure_type, "tool_status")
        missing = judge.evaluate(run_result([call("search_web", data_count=0)]))
        self.assertEqual(missing.status, "unassessable")

    def test_unobserved_arguments_excluded_from_denominator(self):
        """验证未观测参数不计入参数准确率分母。"""
        judge = ToolExecutionJudge(tool_expectations=[{"tool_name": "search_amazon", "parameters": {"limit": 10}}])
        seen = judge.evaluate(run_result([call("search_amazon", parameters={})]))
        self.assertEqual(seen.failure_type, "tool_arguments")
        unknown = judge.evaluate(run_result([call("search_amazon")]))
        self.assertEqual(unknown.status, "unassessable")
        self.assertEqual(unknown.details["parameter_total"], 0)
        self.assertEqual(unknown.details["parameter_unknown"], 1)

    def test_partial_order_checked_without_serializing_parallel_workers(self):
        """验证部分顺序约束可校验且不强制并行工作者串行。"""
        judge = ToolExecutionJudge(tool_order=[["search_web", "fetch_web_page"]])
        search = call("search_web", data_count=1, sequence=1)
        fetch = call("fetch_web_page", data_count=1, sequence=2)
        self.assertTrue(judge.evaluate(run_result([fetch, search])).passed)
        unordered = judge.evaluate(run_result([search, {**fetch, "sequence": None}]))
        self.assertEqual(unordered.status, "unassessable")
        wrong = judge.evaluate(run_result([{**search, "sequence": 3}, fetch]))
        self.assertEqual(wrong.failure_type, "tool_order")

    def test_incomplete_trace_does_not_certify_absence_or_missing_tool(self):
        """验证轨迹不完整时不对缺失或禁用工具下断言。"""
        judge = ToolBoundaryJudge(allowed_tools=None, forbidden_tools=["search_amazon"])
        incomplete = judge.evaluate(run_result(tool_trace_complete=False))
        self.assertEqual(incomplete.status, "unassessable")
        known = judge.evaluate(run_result([call("search_amazon")], tool_trace_complete=False))
        self.assertTrue(known.hard_failure)
        uncertain = judge.evaluate(run_result([call("search_amazon", dispatched=None, data_count=1),
                                               call("search_web", data_count=4)]))
        self.assertEqual(uncertain.status, "unassessable")
        self.assertEqual(uncertain.details["selection_unknown"], 1)
        self.assertEqual(uncertain.details["selection_total"], 1)
        self.assertEqual(uncertain.details["forbidden_executions"], 0)


    def test_missing_dispatch_is_unknown_even_with_nonzero_count(self):
        """验证缺少派发标记时即使数据量非零仍判为未知。"""
        case = {"required_tools": ["search_web"], "forbidden_tools": ["search_amazon"]}
        calls = [{"tool_name": name, "data_count": 2} for name in ("search_web", "search_amazon")]
        checks = evaluate_case(case, run_result(calls, "概况 供应链 建议"))
        trajectory = next(check for check in checks if check.dimension == "trajectory")
        boundary = next(check for check in checks if check.dimension == "governance")
        self.assertEqual(trajectory.status, "unassessable")
        self.assertEqual(trajectory.details["required_tool_hits"], 0)
        self.assertEqual(boundary.status, "unassessable")
        self.assertEqual(boundary.details["forbidden_executions"], 0)


class OutcomeContractTests(unittest.TestCase):
    """结果与治理维度判定的单元测试集合。"""

    def test_fact_requires_same_paragraph_citation_and_observed_evidence(self):
        """验证参考事实须在同段落引用且引用已观测证据。"""
        judge = ReferenceFactJudge([{"id": "price", "any_of": ["20 元"], "evidence_ids": ["SYN-A"]}], [])
        self.assertTrue(judge.evaluate(run_result(report="20 元 [SYN-A]", evidence_ids=["SYN-A"])).passed)
        for amount in ("120 元", "-20 元", "−20 元"):
            with self.subTest(amount=amount):
                self.assertEqual(judge.evaluate(run_result(report=amount + " [SYN-A]", evidence_ids=["SYN-A"])).failure_type,
                                 "reference_fact")
        for text, ids in [("20 元\n\n[SYN-A]", ["SYN-A"]), ("20 元 [SYN-A]", [])]:
            with self.subTest(text=text, ids=ids):
                self.assertEqual(judge.evaluate(run_result(report=text, evidence_ids=ids)).failure_type, "evidence_citation")
        self.assertEqual(judge.evaluate(run_result(report="20 元 [SYN-A]")).status, "unassessable")
        self.assertEqual(judge.evaluate(run_result(report="- 20 元\n- 其它 [SYN-A]", evidence_ids=["SYN-A"])).failure_type,
                         "evidence_citation")
        amount = ReferenceFactJudge([{"id": "price", "any_of": ["200 元"]}], [])
        self.assertEqual(amount.evaluate(run_result(report="1,200 元")).failure_type, "reference_fact")

    def test_forbidden_conclusion_remains_blocking_with_missing_evidence(self):
        """验证禁用结论即使证据缺失仍构成硬失败。"""
        judge = ReferenceFactJudge([{"id": "price", "any_of": ["20 元"], "evidence_ids": ["SYN-A"]}], [r"净赚\s*20"])
        verdict = judge.evaluate(run_result(report="净赚 20 元 [SYN-A]"))
        self.assertEqual(verdict.failure_type, "forbidden_claim")
        self.assertTrue(verdict.hard_failure)
        self.assertEqual(verdict.details["fact_hits"], 0)

    def test_failure_attribution_and_absent_steering(self):
        """验证失败归因唯一，且未观测干预时判为未知。"""
        case = {"required_tools": ["search_web"], "steering_keyword": "新条件"}
        failed = evaluate_case(case, run_result(system_status="FAILED", error="TimeoutError"))
        self.assertEqual([v.failure_type for v in failed], ["run_status"])
        self.assertEqual(evaluate_case(case, run_result(system_status="UNKNOWN"))[0].status, "unassessable")
        judgments = evaluate_case(case, run_result(report="新条件"))
        steering = next(v for v in judgments if v.judge_name == "SteeringJudge")
        self.assertEqual(steering.status, "unassessable")

    def test_trajectory_and_governance_counts_have_one_owner(self):
        """验证轨迹与治理计数各有唯一归属维度。"""
        case = {"required_tools": ["search_web"], "forbidden_tools": ["search_amazon"], "min_chars": 1}
        obs = run_result([call("search_web", data_count=2)], "概况 供应链 建议")
        verdicts = evaluate_case(case, obs)
        self.assertTrue(all(v.passed for v in verdicts))
        owners = [v for v in verdicts if "selection_total" in v.details]
        self.assertEqual([(v.dimension, v.details["selection_total"]) for v in owners], [("governance", 1)])
        owners = [v for v in verdicts if "required_tool_total" in v.details]
        self.assertEqual([(v.dimension, v.details["required_tool_total"]) for v in owners], [("trajectory", 1)])

    def test_incomplete_run_preserves_observed_hard_failures(self):
        """验证运行不完整时仍保留已观测的硬失败。"""
        case = {"required_tools": ["search_web"], "forbidden_tools": ["search_amazon"],
                "forbidden_claims": ["保证净赚"]}
        partial = run_result([call("search_amazon", sequence=1)], "保证净赚", system_status="TIMEOUT")
        failures = evaluate_case(case, partial)
        self.assertEqual([v.failure_type for v in failures], ["run_status", "tool_forbidden", "forbidden_claim"])
        self.assertTrue(failures[1].hard_failure)
        self.assertTrue(failures[2].hard_failure)
