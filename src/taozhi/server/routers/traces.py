"""工作台成员可见的提问、执行记录和管理员发起人筛选。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from taozhi.application.services import trace_service
from taozhi.schemas.trace import SpanKind, TraceActivityPage, TraceDetail, TraceKind, TraceOwnerPage, TracePage, TraceRunPage, TraceSpanPage, TraceState
from taozhi.server.auth import identity

router = APIRouter(prefix="/api/admin/traces", tags=["traces"])
TaskId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[^\x00]+$")]


@router.get("", response_model=TracePage)
def traces(
    kind: TraceKind | None = None,
    state: TraceState | None = None,
    owner_id: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[^\x00]+$"),
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(30, ge=1, le=100),
    p=Depends(identity),
):
    """成员搜索本人任务，管理员可检索全租户或指定发起人的问答与调研。"""
    return trace_service.list_traces(p, kind=kind, state=state, query=q, cursor=cursor, limit=limit, owner_id=owner_id)


@router.get("/owners", response_model=TraceOwnerPage)
def trace_owners(
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    cursor: str | None = Query(None, min_length=1, max_length=256, pattern=r"^[^\x00]+$"),
    limit: int = Query(50, ge=1, le=100),
    p=Depends(identity),
):
    """为管理员发起人筛选提供本企业成员列表。"""
    return trace_service.list_owners(p, query=q, cursor=cursor, limit=limit)


@router.get("/{task_id}", response_model=TraceDetail)
def trace(
    task_id: TaskId,
    after_event_id: int = Query(0, ge=0, le=9223372036854775807),
    event_limit: int = Query(100, ge=1, le=200),
    p=Depends(identity),
):
    """获取该提问跨执行版本的一页状态事件。"""
    return trace_service.get_trace(p, task_id, after_event_id=after_event_id, event_limit=event_limit)


@router.get("/{task_id}/runs", response_model=TraceRunPage)
def trace_runs(
    task_id: TaskId,
    cursor: str | None = Query(None, max_length=64),
    trace_id: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
    limit: int = Query(30, ge=1, le=100),
    p=Depends(identity),
):
    """按执行版本列出实际采集的链路，支持定位检索命中的链路。"""
    return trace_service.list_runs(p, task_id, cursor=cursor, trace_id=trace_id, limit=limit)


@router.get("/{task_id}/runs/{trace_id}/spans", response_model=TraceSpanPage)
def trace_spans(
    task_id: TaskId,
    trace_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    kind: SpanKind | None = None,
    cursor: int = Query(0, ge=0, le=9223372036854775807),
    limit: int = Query(100, ge=1, le=500),
    p=Depends(identity),
):
    """读取指定 Trace ID 的实际节点，按 OpenInference 类型独立翻页。"""
    return trace_service.list_spans(p, task_id, trace_id, kind=kind, after_id=cursor, limit=limit)


@router.get("/{task_id}/runs/{trace_id}/activities", response_model=TraceActivityPage)
def trace_activities(
    task_id: TaskId,
    trace_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    kind: SpanKind | None = None,
    cursor: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    p=Depends(identity),
):
    """按开始时间读取关键行动，可按节点类型查看该链路的具体执行者。"""
    return trace_service.list_activities(p, task_id, trace_id, kind=kind, cursor=cursor, limit=limit)
