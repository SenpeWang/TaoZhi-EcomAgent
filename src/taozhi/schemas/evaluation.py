"""工作台评测中心的数据投影：数据集版本、算法版本、基线、批次与指标，排除提示词与模型原文。"""
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field


EvaluationKind = Literal["agent", "rag"]
EvaluationMode = Literal["live", "synthetic_trace_regrade"]
EvaluationScope = Literal["full", "quick", "retrieval", "adversarial", "behavior"]
EvaluationStatus = Literal["passed", "failed", "unassessable"]
EvaluationAuditAction = Literal["register_dataset", "register_profile", "trigger_run", "publish_run", "set_baseline", "purge_data", "migrate_dataset_versions"]
EvaluationJobState = Literal["queued", "running", "review", "completed", "cancelled", "failed", "rejected", "legacy"]


class EvaluationJudgeSummary(BaseModel):
    """单个判定器在某一维度上的通过、失败与不可判定计数。"""
    judge: str
    dimension: str
    passed: int
    failed: int
    unassessable: int


class EvaluationMetricSpec(BaseModel):
    """单个评测指标的展示口径：键、名称、分组、单位、方向与说明。"""
    key: str
    label: str
    group: str
    unit: str
    direction: str
    description: str = ""


class EvaluationMetricSchema(BaseModel):
    """某评测类型与口径的唯一指标集合定义。"""
    version: int
    eval_kind: EvaluationKind
    scope: EvaluationScope
    label: str
    metrics: list[EvaluationMetricSpec]


class EvaluationMetricScope(BaseModel):
    """单个口径的指标集合投影，供指标目录按口径展示。"""
    version: int
    eval_kind: EvaluationKind
    scope: EvaluationScope
    label: str
    metrics: list[EvaluationMetricSpec]


class EvaluationMetricKind(BaseModel):
    """单一评测类型下的全部口径指标定义。"""
    label: str
    scopes: dict[str, EvaluationMetricScope]


class EvaluationMetricCatalog(BaseModel):
    """按评测类型组织的完整指标目录。"""
    version: int
    kinds: dict[str, EvaluationMetricKind]


class EvaluationRun(BaseModel):
    """评测批次投影：归属、数据集、算法版本、判定结果与主指标。"""
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
    dataset_snapshot_id: str | None = None
    run_seq: int | None = None
    parent_run_id: str | None = None
    trigger_note: str | None = None
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
    """数据集版本投影：登记信息、用例数与批次聚合指标。"""
    dataset_id: str
    name: str
    eval_kind: EvaluationKind = "agent"
    version: str = "v1"
    description: str = ""
    source: EvaluationMode
    suite: str = ""
    archived: bool = False
    execution_modes: list[str] = Field(default_factory=list)
    content_sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    case_count: int
    batch_count: int = 0
    last_run_at: datetime | None = None
    avg_pass_rate: float | None = None
    best_pass_rate: float | None = None
    passed_batches: int = 0


class EvaluationDatasetDetail(BaseModel):
    """数据集版本详情：元信息与按游标分页的用例清单。"""
    dataset: EvaluationDataset
    cases: list[dict[str, Any]]
    next_cursor: str | None = None
    total_cases: int = 0


class EvaluationDatasetPage(BaseModel):
    """数据集版本列表页。"""
    items: list[EvaluationDataset]


class EvaluationDatasetRaw(BaseModel):
    """数据集原始登记内容：元信息、文件摘要与全部原始用例。"""
    dataset: EvaluationDataset
    files: dict[str, str] = Field(default_factory=dict)
    cases: list[dict[str, Any]]


class EvaluationProfile(BaseModel):
    """不可变算法版本投影：技术栈、检索参数、Prompt 快照与批次聚合指标。"""
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
    """算法版本列表页。"""
    items: list[EvaluationProfile]


class CreateEvalProfileInput(BaseModel):
    """登记算法版本的请求体：技术栈、检索参数、Prompt 快照与备注。"""
    eval_kind: EvaluationKind
    version_tag: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_.-]*$")
    tech_stack: dict[str, Any] = Field(default_factory=dict)
    retrieval_params: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshots: dict[str, Any] = Field(default_factory=dict)
    git_sha: str | None = Field(default=None, max_length=64)
    dataset_version: str = Field(default="", max_length=32)
    notes: str = Field(default="", max_length=1000)


class EvaluationBaseline(BaseModel):
    """基线投影：绑定批次、口径与执行方式，并带原批次指标摘要。"""
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
    """基线列表页。"""
    items: list[EvaluationBaseline]


class SetBaselineInput(BaseModel):
    """设置基线的请求体：批次编号与说明。"""
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    note: str = Field(default="", max_length=500)


class TriggerEvaluationInput(BaseModel):
    """触发评测任务的请求体：数据集、算法版本、口径与执行方式。"""
    eval_kind: EvaluationKind
    dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    profile_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_.-]*$")
    scope: EvaluationScope = "full"
    mode: EvaluationMode = "live"
    note: str = Field(default="", max_length=500)
    parent_run_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_.-]*$")
    mark_baseline: bool = False


class TriggerReceipt(BaseModel):
    """触发评测任务的回执：任务、审计编号与初始状态。"""
    job_id: str
    audit_id: str
    status: str


class EvaluationJob(BaseModel):
    """评测任务状态投影：阶段进度、终态结果与时间戳。"""
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
    """评测写入审计留痕条目。"""
    audit_id: str
    tenant_id: str
    actor_id: str
    action: EvaluationAuditAction
    target_id: str
    git_sha: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EvaluationAuditPage(BaseModel):
    """审计留痕分页页。"""
    items: list[EvaluationAuditLog]
    next_cursor: str | None = None


class EvaluationTrendItem(BaseModel):
    """趋势序列中的单批次主指标点，含相对基线的差值。"""
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
    """趋势序列的批次数量与通过率汇总。"""
    batches: int
    avg_pass_rate: float | None = None
    best_pass_rate: float | None = None
    latest_pass_rate: float | None = None


class EvaluationBaselineSummary(BaseModel):
    """趋势接口返回的 active 基线摘要。"""
    baseline_id: str
    run_id: str
    note: str = ""
    created_at: datetime | None = None
    pass_rate: float | None = None
    gate_passed: bool | None = None
    report_sha256: str | None = None


class EvaluationTrends(BaseModel):
    """批次主指标趋势：指标 schema、序列、汇总与基线对照。"""
    metric_schema: EvaluationMetricSchema | None = None
    dataset_version: str | None = None
    scope: EvaluationScope | None = None
    mode: EvaluationMode | None = None
    items: list[EvaluationTrendItem]
    aggregate: EvaluationTrendAggregate
    baseline: EvaluationBaselineSummary | None = None


class EvaluationLayers(BaseModel):
    """批次分层指标：检索、对抗、行为与六项工程指标的原始值。"""
    run_id: str
    eval_kind: EvaluationKind
    layers: dict[str, Any]


class EvaluationRunPage(BaseModel):
    """评测批次列表页。"""
    items: list[EvaluationRun]
    next_cursor: str | None


class EvaluationCase(BaseModel):
    """批次逐例判定摘要：用例、状态、分值与硬失败标记。"""
    case_id: str
    name: str
    category: str | None
    suite: str
    status: EvaluationStatus
    score: float | None
    hard_failure: bool


class EvaluationCasePage(BaseModel):
    """批次逐例判定分页页。"""
    items: list[EvaluationCase]
    next_cursor: str | None


class EvaluationIssue(BaseModel):
    """单条判定问题：失败类型、原因、工具与步骤位置。"""
    failure_type: str
    reason: str
    tool_name: str | None
    step: int | None


class EvaluationVerdict(BaseModel):
    """单个判定器对用例的判定结论与问题清单。"""
    judge: str
    dimension: str
    status: EvaluationStatus
    reason: str
    issues: list[EvaluationIssue]


class EvaluationStep(BaseModel):
    """用例观测到的工具调用步骤及其参数匹配信息。"""
    step: int | None
    tool_name: str
    status: str | None
    dispatched: bool | None
    data_count: int | None
    parameters_match: bool | None
    parameter_keys: list[str] | None


class EvaluationTraceLink(BaseModel):
    """用例执行链路与任务编号的关联。"""
    task_id: str
    trace_id: str


class EvaluationCaseDetail(BaseModel):
    """用例详情：判定摘要、预期与观测、判定结论、链路与快照原文。"""
    case: EvaluationCase
    expected: list[str]
    observed: list[EvaluationStep] | dict[str, Any] | None
    steps_complete: bool | None
    verdicts: list[EvaluationVerdict]
    trace_link: EvaluationTraceLink | None
    dataset_case: dict[str, Any] | None = None
    dataset_case_sha256: str | None = None


class EvaluationComparison(BaseModel):
    """两个批次的逐例配对比较结果与排除统计。"""
    baseline_sha256: str
    comparable: bool
    passed: bool
    paired_counts: dict[str, int]
    changes: dict[str, dict[str, list[str]]]
    excluded_count: int
    excluded: dict[str, list[str]]


class EvaluationRatio(BaseModel):
    """分子、分母与比率的通用比例投影。"""
    numerator: int
    denominator: int
    rate: float | None


class EvaluationRunDetail(BaseModel):
    """批次详情：批次投影、逐例对比、原始指标与数据集快照。"""
    run: EvaluationRun
    comparison: EvaluationComparison | None
    metrics: dict[str, Any]
    metric_schema: EvaluationMetricSchema | None = None
    dataset_snapshot: dict[str, Any] | None = None


class EvaluationRunSnapshotPage(BaseModel):
    """批次数据集快照分页页：快照元信息与原始用例。"""
    snapshot: dict[str, Any]
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    total: int
