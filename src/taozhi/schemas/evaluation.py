"""工作台评测报告的只读投影，排除提示词、工具参数值和模型原文。"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


EvaluationKind = Literal["agent", "rag"]
EvaluationMode = Literal["live", "synthetic_trace_regrade"]
EvaluationStatus = Literal["passed", "failed", "unassessable"]


class EvaluationJudgeSummary(BaseModel):
    judge: str
    dimension: str
    passed: int
    failed: int
    unassessable: int


class EvaluationRun(BaseModel):
    run_id: str
    owner_id: str
    owner_name: str
    created_at: datetime
    mode: EvaluationMode
    git_sha: str | None
    dataset_sha256: str
    judges_sha256: str
    transcript_sha256: str | None
    counterexamples_sha256: str | None
    model_config_sha256: str | None
    gate_passed: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    unassessable_cases: int
    dataset_id: str | None = None
    dataset_name: str | None = None
    eval_kind: EvaluationKind = "agent"
    profile_id: str | None = None
    profile_version_tag: str | None = None
    pass_rate: float | None = None
    mean_score: float | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    total_tokens: int | None = None
    judges: list[EvaluationJudgeSummary] | None = None


class EvaluationDataset(BaseModel):
    dataset_id: str
    name: str
    eval_kind: EvaluationKind = "agent"
    version: str = "v1"
    description: str = ""
    source: EvaluationMode
    case_count: int
    batch_count: int = 0
    last_run_at: datetime | None = None
    avg_pass_rate: float | None = None
    best_pass_rate: float | None = None
    passed_batches: int = 0


class EvaluationDatasetDetail(BaseModel):
    dataset: EvaluationDataset
    cases: list[dict[str, Any]]


class EvaluationDatasetPage(BaseModel):
    items: list[EvaluationDataset]


class EvaluationProfile(BaseModel):
    profile_id: str
    eval_kind: EvaluationKind
    version_tag: str
    tech_stack: dict[str, Any]
    retrieval_params: dict[str, Any]
    prompt_snapshots: dict[str, Any]
    prompt_sha256: str
    notes: str
    created_by: str
    created_at: datetime
    batch_count: int = 0
    avg_pass_rate: float | None = None
    avg_mean_score: float | None = None
    avg_latency_p50_ms: float | None = None
    avg_tokens: float | None = None


class EvaluationProfilePage(BaseModel):
    items: list[EvaluationProfile]


class CreateEvalProfileInput(BaseModel):
    eval_kind: EvaluationKind
    version_tag: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    tech_stack: dict[str, Any] = Field(default_factory=dict)
    retrieval_params: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshots: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=1000)


class TriggerEvaluationInput(BaseModel):
    eval_kind: EvaluationKind
    dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    profile_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    note: str = Field(default="", max_length=500)


class EvaluationTriggerReceipt(BaseModel):
    audit_id: str
    run_id: str | None = None
    status: str


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
    reason: str
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
    expected: list[str]
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
    required_tool_coverage: EvaluationRatio
    parameter_accuracy: EvaluationRatio
    reference_fact_coverage: EvaluationRatio
    call_coverage_among_known: EvaluationRatio
    measured_prompt_tokens: int | None
    measured_completion_tokens: int | None


class EvaluationRunDetail(BaseModel):
    run: EvaluationRun
    comparison: EvaluationComparison | None
    metrics: EvaluationMetrics
