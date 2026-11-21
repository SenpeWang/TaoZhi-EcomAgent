"""确定性验证离线回放的断言、缺测与严重失败语义。"""
from __future__ import annotations

import unittest

from evals.agent_eval.harness import AgentRunResult
from evals.agent_eval.judges import ReferenceFactJudge, ToolBoundaryJudge, ToolExecutionJudge, evaluate_case


def run_result(calls=(), report="", **fields):
    return AgentRunResult(report=report, tool_calls=list(calls), system_status=fields.pop("system_status", "COMPLETED"), **fields)


def call(name, **fields):
    return {"tool_name": name, "dispatched": True, **fields}


class ToolContractTests(unittest.TestCase):
    def test_each_required_source_must_provide_data(self):
        judge = ToolExecutionJudge(required_tools=["search_web", "search_amazon"])
        res = run_result([call("search_web", status="ok", data_count=11),
                          call("search_amazon", status="empty", data_count=0)])
        self.assertEqual(judge.evaluate(res).failure_type, "tool_data")

    def test_status_and_arguments_are_checked_on_the_same_call(self):
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
        judge = ToolExecutionJudge(tool_expectations=[{"tool_name": "search_web", "status": "empty", "min_data_count": 0}])
        self.assertTrue(judge.evaluate(run_result([call("search_web", status="empty", data_count=0)])).passed)
        wrong = judge.evaluate(run_result([call("search_web", status="timeout", data_count=0)]))
        self.assertEqual(wrong.failure_type, "tool_status")
        missing = judge.evaluate(run_result([call("search_web", data_count=0)]))
        self.assertEqual(missing.status, "unassessable")

    def test_unobserved_arguments_excluded_from_denominator(self):
        judge = ToolExecutionJudge(tool_expectations=[{"tool_name": "search_amazon", "parameters": {"limit": 10}}])
        seen = judge.evaluate(run_result([call("search_amazon", parameters={})]))
        self.assertEqual(seen.failure_type, "tool_arguments")
        unknown = judge.evaluate(run_result([call("search_amazon")]))
        self.assertEqual(unknown.status, "unassessable")
        self.assertEqual(unknown.details["parameter_total"], 0)
        self.assertEqual(unknown.details["parameter_unknown"], 1)

    def test_partial_order_checked_without_serializing_parallel_workers(self):
        judge = ToolExecutionJudge(tool_order=[["search_web", "fetch_web_page"]])
        search = call("search_web", data_count=1, sequence=1)
        fetch = call("fetch_web_page", data_count=1, sequence=2)
        self.assertTrue(judge.evaluate(run_result([fetch, search])).passed)
        unordered = judge.evaluate(run_result([search, {**fetch, "sequence": None}]))
        self.assertEqual(unordered.status, "unassessable")
        wrong = judge.evaluate(run_result([{**search, "sequence": 3}, fetch]))
        self.assertEqual(wrong.failure_type, "tool_order")

    def test_incomplete_trace_does_not_certify_absence_or_missing_tool(self):
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
    def test_fact_requires_same_paragraph_citation_and_observed_evidence(self):
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
        judge = ReferenceFactJudge([{"id": "price", "any_of": ["20 元"], "evidence_ids": ["SYN-A"]}], [r"净赚\s*20"])
        verdict = judge.evaluate(run_result(report="净赚 20 元 [SYN-A]"))
        self.assertEqual(verdict.failure_type, "forbidden_claim")
        self.assertTrue(verdict.hard_failure)
        self.assertEqual(verdict.details["fact_hits"], 0)

    def test_failure_attribution_and_absent_steering(self):
        case = {"required_tools": ["search_web"], "steering_keyword": "新条件"}
        failed = evaluate_case(case, run_result(system_status="FAILED", error="TimeoutError"))
        self.assertEqual([v.failure_type for v in failed], ["run_status"])
        self.assertEqual(evaluate_case(case, run_result(system_status="UNKNOWN"))[0].status, "unassessable")
        judgments = evaluate_case(case, run_result(report="新条件"))
        steering = next(v for v in judgments if v.judge_name == "SteeringJudge")
        self.assertEqual(steering.status, "unassessable")

    def test_trajectory_and_governance_counts_have_one_owner(self):
        case = {"required_tools": ["search_web"], "forbidden_tools": ["search_amazon"], "min_chars": 1}
        obs = run_result([call("search_web", data_count=2)], "概况 供应链 建议")
        verdicts = evaluate_case(case, obs)
        self.assertTrue(all(v.passed for v in verdicts))
        owners = [v for v in verdicts if "selection_total" in v.details]
        self.assertEqual([(v.dimension, v.details["selection_total"]) for v in owners], [("governance", 1)])
        owners = [v for v in verdicts if "required_tool_total" in v.details]
        self.assertEqual([(v.dimension, v.details["required_tool_total"]) for v in owners], [("trajectory", 1)])

    def test_incomplete_run_preserves_observed_hard_failures(self):
        case = {"required_tools": ["search_web"], "forbidden_tools": ["search_amazon"],
                "forbidden_claims": ["保证净赚"]}
        partial = run_result([call("search_amazon", sequence=1)], "保证净赚", system_status="TIMEOUT")
        failures = evaluate_case(case, partial)
        self.assertEqual([v.failure_type for v in failures], ["run_status", "tool_forbidden", "forbidden_claim"])
        self.assertTrue(failures[1].hard_failure)
        self.assertTrue(failures[2].hard_failure)
