"""轨迹中台只读响应，显式限定允许跨用户展示的字段。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
    matched_run_version: int | None = None


class TracePage(BaseModel):
    """按时间倒序排列的提问和后续分页游标。"""

    items: list[Trace]
    next_cursor: str | None


class TraceEvent(BaseModel):
    """一次状态变更或执行步骤。"""

    id: int
    run_version: int
    stage: str
    progress: int
    status: str
    created_at: datetime


class TraceModelCall(BaseModel):
    """模型调用的记账信息。"""

    id: int
    role: str
    role_label: str | None = None
    elapsed_ms: int
    prompt_tokens: int
    completion_tokens: int
    outcome: str
    created_at: datetime


class TraceDetail(BaseModel):
    """任务详情、时间线及关联的模型调用。"""

    trace: Trace
    trace_id: str | None
    events: list[TraceEvent]
    has_more_events: bool
    model_calls: list[TraceModelCall]
    has_more_calls: bool


SpanStatus = Literal["running", "ok", "error", "interrupted"]
SpanKind = Literal["CHAIN", "AGENT", "LLM", "TOOL", "RETRIEVER", "INTERNAL"]


class TraceRun(BaseModel):
    """单次任务执行所记录的链路概况。"""

    run_version: int
    trace_id: str
    started_at: datetime
    ended_at: datetime | None
    status: SpanStatus
    span_count: int
    failed_count: int
    input_tokens: int
    output_tokens: int


class TraceRunPage(BaseModel):
    """按执行版本从新到旧排列的链路。"""

    items: list[TraceRun]
    next_before_version: int | None


class TraceSpan(BaseModel):
    """节点时间边界、父子关系和允许展示的遥测元数据。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    display_name: str | None = None
    kind: SpanKind
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    status: SpanStatus
    error_type: str | None
    attributes: dict[str, str | int | float | bool]


class TraceSpanPage(BaseModel):
    """单条链路的一页节点，游标独立于节点完成状态。"""

    items: list[TraceSpan]
    next_cursor: str | None
    observed_at: datetime
