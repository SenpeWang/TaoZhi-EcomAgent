"""工作台成员可见的提问、执行记录和管理员发起人筛选。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from taozhi.application.services import trace_service
from taozhi.schemas.trace import TraceDetail, TraceKind, TraceOwnerPage, TracePage, TraceRunPage, TraceSpanPage, TraceState
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
    before_call_id: int | None = Query(None, ge=1, le=9223372036854775807),
    p=Depends(identity),
):
    """获取任务时间线的一页及该任务的模型用量账本。"""
    return trace_service.get_trace(
        p, task_id, after_event_id=after_event_id, event_limit=event_limit, before_call_id=before_call_id
    )


@router.get("/{task_id}/runs", response_model=TraceRunPage)
def trace_runs(
    task_id: TaskId,
    before_version: int | None = Query(None, ge=0, le=9223372036854775807),
    limit: int = Query(30, ge=1, le=100),
    p=Depends(identity),
):
    """查看某个提问已采集的执行版本，包含正在进行的执行。"""
    return trace_service.list_runs(p, task_id, before_version=before_version, limit=limit)


@router.get("/{task_id}/runs/{run_version}/spans", response_model=TraceSpanPage)
def trace_spans(
    task_id: TaskId,
    run_version: Annotated[int, Path(ge=0, le=9223372036854775807)],
    cursor: int = Query(0, ge=0, le=9223372036854775807),
    limit: int = Query(100, ge=1, le=500),
    p=Depends(identity),
):
    """按节点创建顺序读取选中执行的调用链，支持重新读取已完成的节点。"""
    return trace_service.list_spans(p, task_id, run_version, after_id=cursor, limit=limit)
