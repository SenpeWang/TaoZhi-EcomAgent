"""工作台评测中心的数据投影：数据集版本、算法版本、基线、批次与指标，排除提示词与模型原文。"""
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field


EvaluationKind = Literal["agent", "rag"]
EvaluationMode = Literal["live", "synthetic_trace_regrade"]
EvaluationScope = Literal["full", "quick", "retrieval", "adversarial", "behavior"]
EvaluationStatus = Literal["passed", "failed", "unassessable"]
EvaluationAuditAction = Literal["register_dataset", "register_profile", "trigger_run", "publish_run", "set_baseline", "purge_data"]
EvaluationJobState = Literal["queued", "running", "review", "completed", "cancelled", "failed", "rejected", "legacy"]


class EvaluationJudgeSummary(BaseModel):
    judge: str
    dimension: str
    passed: int
    failed: int
    unassessable: int


class EvaluationMetricSpec(BaseModel):
    key: str
    label: str
    group: str
    unit: str
    direction: str
    description: str = ""


class EvaluationMetricSchema(BaseModel):
    version: int
    eval_kind: EvaluationKind
    scope: EvaluationScope
    label: str
    metrics: list[EvaluationMetricSpec]


class EvaluationMetricScope(BaseModel):
    version: int
    eval_kind: EvaluationKind
    scope: EvaluationScope
    label: str
    metrics: list[EvaluationMetricSpec]


class EvaluationMetricKind(BaseModel):
    label: str
    scopes: dict[str, EvaluationMetricScope]


class EvaluationMetricCatalog(BaseModel):
    version: int
    kinds: dict[str, EvaluationMetricKind]


class EvaluationRun(BaseModel):
    run_id: str
    owner_id: str
    owner_name: str
    created_at: datetime
    mode: EvaluationMode
    scope: EvaluationScope = "full"
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
    dataset_version: str | None = None
    eval_kind: EvaluationKind = "agent"
    profile_id: str | None = None
    profile_version_tag: str | None = None
    profile_version_no: int | None = None
    is_baseline: bool = False
    report_available: bool = False
    comparable: bool = True
    pass_rate: float | None = None
    mean_score: float | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    total_tokens: int | None = None
    judges: list[EvaluationJudgeSummary] | None = None
    summary: dict[str, float | None] | None = None
    metric_schema_version: int | None = None


class EvaluationDataset(BaseModel):
    dataset_id: str
    name: str
    eval_kind: EvaluationKind = "agent"
    version: str = "v1"
    description: str = ""
    source: EvaluationMode
    suite: str = ""
    archived: bool = False
    content_sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    case_count: int
    batch_count: int = 0
    last_run_at: datetime | None = None
    avg_pass_rate: float | None = None
    best_pass_rate: float | None = None
    passed_batches: int = 0


class EvaluationDatasetDetail(BaseModel):
    dataset: EvaluationDataset
    cases: list[dict[str, Any]]
    next_cursor: str | None = None
    total_cases: int = 0


class EvaluationDatasetPage(BaseModel):
    items: list[EvaluationDataset]


class EvaluationDatasetRaw(BaseModel):
    dataset: EvaluationDataset
    files: dict[str, str] = Field(default_factory=dict)
    cases: list[dict[str, Any]]


class EvaluationProfile(BaseModel):
    profile_id: str
    eval_kind: EvaluationKind
    version_no: int = 0
    version_tag: str
    digest: str = ""
    git_sha: str | None = None
    dataset_version: str = ""
    tech_stack: dict[str, Any]
    retrieval_params: dict[str, Any]
    prompt_snapshots: dict[str, Any]
    prompt_sha256: str
    notes: str
    created_by: str
    created_at: datetime
    created: bool = False
    batch_count: int = 0
    avg_pass_rate: float | None = None
    avg_mean_score: float | None = None
    avg_latency_p50_ms: float | None = None
    avg_tokens: float | None = None


class EvaluationProfilePage(BaseModel):
    items: list[EvaluationProfile]


class CreateEvalProfileInput(BaseModel):
    eval_kind: EvaluationKind
    version_tag: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_.-]*$")
    tech_stack: dict[str, Any] = Field(default_factory=dict)
    retrieval_params: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshots: dict[str, Any] = Field(default_factory=dict)
    git_sha: str | None = Field(default=None, max_length=64)
    dataset_version: str = Field(default="", max_length=32)
    notes: str = Field(default="", max_length=1000)


class EvaluationBaseline(BaseModel):
    baseline_id: str
    eval_kind: EvaluationKind
    dataset_id: str
    dataset_version: str
    scope: EvaluationScope
    mode: EvaluationMode
    run_id: str
    note: str
    created_by: str
    created_at: datetime
    active: bool
    superseded_at: datetime | None = None
    run_created_at: datetime | None = None
    run_gate_passed: bool | None = None
    run_pass_rate: float | None = None
    run_git_sha: str | None = None
    run_report_sha256: str | None = None
    algorithm_version: str | None = None


class EvaluationBaselinePage(BaseModel):
    items: list[EvaluationBaseline]


class SetBaselineInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    note: str = Field(default="", max_length=500)


class TriggerEvaluationInput(BaseModel):
    eval_kind: EvaluationKind
    dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    profile_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_.-]*$")
    scope: EvaluationScope = "full"
    mode: EvaluationMode = "live"
    note: str = Field(default="", max_length=500)
    mark_baseline: bool = False


class TriggerReceipt(BaseModel):
    job_id: str
    audit_id: str
    status: str


class EvaluationJob(BaseModel):
    job_id: str
    state: EvaluationJobState
    stage: str
    progress: int
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvaluationAuditLog(BaseModel):
    audit_id: str
    tenant_id: str
    actor_id: str
    action: EvaluationAuditAction
    target_id: str
    git_sha: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EvaluationAuditPage(BaseModel):
    items: list[EvaluationAuditLog]
    next_cursor: str | None = None


class EvaluationTrendItem(BaseModel):
    run_id: str
    created_at: datetime
    mode: EvaluationMode
    scope: EvaluationScope
    dataset_version: str | None = None
    algorithm_version: str | None = None
    gate_passed: bool
    pass_rate: float | None = None
    mean_score: float | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    total_tokens: int | None = None
    is_baseline: bool = False
    comparable: bool = True
    pass_rate_delta: float | None = None
    summary: dict[str, float | None] | None = None


class EvaluationTrendAggregate(BaseModel):
    batches: int
    avg_pass_rate: float | None = None
    best_pass_rate: float | None = None
    latest_pass_rate: float | None = None


class EvaluationBaselineSummary(BaseModel):
    baseline_id: str
    run_id: str
    note: str = ""
    created_at: datetime | None = None
    pass_rate: float | None = None
    gate_passed: bool | None = None
    report_sha256: str | None = None


class EvaluationTrends(BaseModel):
    metric_schema: EvaluationMetricSchema | None = None
    dataset_version: str | None = None
    scope: EvaluationScope | None = None
    mode: EvaluationMode | None = None
    items: list[EvaluationTrendItem]
    aggregate: EvaluationTrendAggregate
    baseline: EvaluationBaselineSummary | None = None


class EvaluationLayers(BaseModel):
    run_id: str
    eval_kind: EvaluationKind
    layers: dict[str, Any]


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
    observed: list[EvaluationStep] | dict[str, Any] | None
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


class EvaluationRunDetail(BaseModel):
    run: EvaluationRun
    comparison: EvaluationComparison | None
    metrics: dict[str, Any]
    metric_schema: EvaluationMetricSchema | None = None