"""工作台评测报告的只读投影，排除提示词、工具参数值和模型原文。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


EvaluationMode = Literal["live", "synthetic_trace_regrade"]
EvaluationStatus = Literal["passed", "failed", "unassessable"]


class EvaluationRun(BaseModel):
    run_id: str
    owner_id: str
    owner_name: str
    created_at: datetime
    mode: EvaluationMode
    git_sha: str | None
    dataset_sha256: str
    judges_sha256: str
    gate_passed: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    unassessable_cases: int


class EvaluationRunPage(BaseModel):
    items: list[EvaluationRun]
    next_cursor: str | None


class EvaluationCase(BaseModel):
    case_id: str
    name: str
    category: str | None
    suite: str
    status: EvaluationStatus
    score: float | None
    hard_failure: bool


class EvaluationCasePage(BaseModel):
    items: list[EvaluationCase]
    next_cursor: str | None


class EvaluationIssue(BaseModel):
    failure_type: str
    tool_name: str | None
    step: int | None


class EvaluationVerdict(BaseModel):
    judge: str
    dimension: str
    status: EvaluationStatus
    reason: str
    issues: list[EvaluationIssue]


class EvaluationStep(BaseModel):
    step: int | None
    tool_name: str
    status: str | None
    dispatched: bool | None
    data_count: int | None
    parameters_match: bool | None
    parameter_keys: list[str] | None


class EvaluationTraceLink(BaseModel):
    task_id: str
    trace_id: str


class EvaluationCaseDetail(BaseModel):
    case: EvaluationCase
    expected: list[str] | None
    observed: list[EvaluationStep] | None
    steps_complete: bool | None
    verdicts: list[EvaluationVerdict]
    trace_link: EvaluationTraceLink | None


class EvaluationComparison(BaseModel):
    baseline_sha256: str
    comparable: bool
    passed: bool
    paired_counts: dict[str, int]
    changes: dict[str, dict[str, list[str]]]
    excluded_count: int
    excluded: dict[str, list[str]]


class EvaluationRatio(BaseModel):
    numerator: int
    denominator: int
    rate: float | None


class EvaluationMetrics(BaseModel):
    required_tool_coverage: EvaluationRatio | None = None
    parameter_accuracy: EvaluationRatio | None = None
    reference_fact_coverage: EvaluationRatio | None = None
    call_coverage_among_known: EvaluationRatio | None = None
    measured_prompt_tokens: int | None = None
    measured_completion_tokens: int | None = None


class EvaluationRunDetail(BaseModel):
    run: EvaluationRun
    comparison: EvaluationComparison | None
    metrics: EvaluationMetrics
