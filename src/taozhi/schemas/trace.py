"""轨迹中台只读响应，显式限定允许跨用户展示的字段。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from taozhi.domain.trace_diagnostic import DiagnosticCategory, DiagnosticOutcome
from taozhi.domain.trace_activity import ActivityCategory, SpanKind


TraceKind = Literal["ask", "research"]
TraceState = Literal["queued", "running", "review", "completed", "cancelled", "failed", "rejected", "legacy"]


class Trace(BaseModel):
    """提问、归属和执行状态。"""

    id: str
    kind: TraceKind
    state: TraceState
    question: str
    owner_id: str
    username: str
    display_name: str
    node_name: str
    stage: str
    progress: int
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    matched_trace_id: str | None = None


class TracePage(BaseModel):
    """按时间倒序排列的提问和后续分页游标。"""

    items: list[Trace]
    next_cursor: str | None


class TraceOwner(BaseModel):
    """筛选发起人时显示的账号标识与名称。"""

    id: str
    username: str
    display_name: str


class TraceOwnerPage(BaseModel):
    """管理员当前租户的一页可选发起人。"""

    items: list[TraceOwner]
    next_cursor: str | None


class TraceEvent(BaseModel):
    """一次状态变更或执行步骤。"""

    id: int
    run_version: int
    stage: str
    progress: int
    status: str
    created_at: datetime


class TraceDetail(BaseModel):
    """任务详情与持久化的状态时间线。"""

    trace: Trace
    events: list[TraceEvent]
    has_more_events: bool


SpanStatus = Literal["running", "ok", "error", "interrupted"]


class TraceRun(BaseModel):
    """单次任务执行所记录的链路概况。"""

    run_version: int
    trace_id: str
    started_at: datetime
    ended_at: datetime | None
    status: SpanStatus
    span_count: int
    kind_counts: dict[SpanKind, int]
    category_counts: dict[ActivityCategory, int]
    diagnostic_counts: dict[DiagnosticCategory, int]
    failed_count: int
    input_tokens: int
    output_tokens: int


class TraceRunPage(BaseModel):
    """按执行版本从新到旧排列的链路。"""

    items: list[TraceRun]
    next_cursor: str | None


class TraceDiagnostic(BaseModel):
    """发生在该节点的已分类诊断，保留实际处理方式。"""

    timestamp: datetime
    category: DiagnosticCategory
    code: str
    message: str
    outcome: DiagnosticOutcome
    level: Literal["warning", "error"]
    exception_type: str | None
    http_status: int | None


class TraceSpan(BaseModel):
    """节点时间边界、父子关系和允许展示的遥测元数据。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    display_name: str
    kind: SpanKind
    category: ActivityCategory
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    status: SpanStatus
    error_type: str | None
    diagnostics: list[TraceDiagnostic]
    attributes: dict[str, str | int | float | bool]


class TraceSpanPage(BaseModel):
    """单条链路的一页节点，游标独立于节点完成状态。"""

    items: list[TraceSpan]
    next_cursor: str | None
    observed_at: datetime


class TraceActor(BaseModel):
    """该次执行的 Agent 或工作流节点。"""

    kind: Literal["agent", "workflow"]
    span_id: str
    display_name: str


class TraceActivity(TraceSpan):
    """按发生时间阅读的一次实际操作及其可核实的执行者。"""

    actor: TraceActor | None


class TraceActivityPage(BaseModel):
    """按照开始时间和采集主键稳定排序的一页执行活动。"""

    items: list[TraceActivity]
    next_cursor: str | None
    observed_at: datetime
