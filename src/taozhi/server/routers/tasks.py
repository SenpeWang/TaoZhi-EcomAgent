"""智能问答、协同调研会话、任务控制流（取消/干预/重试/审核/SSE）与工具路由。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from taozhi.application.services import task_service
from taozhi.domain.errors import Denied
from taozhi.schemas.common import Decision
from taozhi.schemas.task import Question, SteerInput, Tool
from taozhi.server.auth import identity

router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/tasks", status_code=202)
def ask(
    payload: Question,
    idempotency_key: str | None = Header(default=None, max_length=128),
    p=Depends(identity),
):
    """提交智能问答或协同调研任务，内容密级由当前登录主体权限自动确定。"""
    data = payload.model_dump(exclude={"question", "node_id", "input_level", "conversation_id"})
    return task_service.submit_task(
        p,
        question=payload.question,
        kind=payload.kind,
        node_id=payload.node_id,
        conversation_id=payload.conversation_id,
        payload_data=data,
        idempotency_key=idempotency_key,
    )


@router.get("/tasks")
def tasks(p=Depends(identity)):
    """列出用户有权查看的问答与协同调研任务摘要。"""
    return {"items": task_service.list_tasks(p)}


@router.get("/research/conversations")
def research_conversations(p=Depends(identity)):
    """列出个人协同调研多轮对话会话清单。"""
    return {"items": task_service.list_research_conversations(p)}


@router.get("/research/conversation/{conversation_id}")
def research_conversation(conversation_id: str, p=Depends(identity)):
    """加载单个协同调研会话的历史轮次、摘要及记忆事实。"""
    return task_service.get_research_conversation(p, conversation_id)


@router.delete("/research/conversation/{conversation_id}")
def delete_research_conversation(conversation_id: str, p=Depends(identity)):
    """删除个人协同调研会话及关联任务与事件流水。"""
    return task_service.delete_research_conversation(p, conversation_id)


@router.get("/tasks/{task_id}")
def task(task_id: str, p=Depends(identity)):
    """获取单个任务详情与完整阶段事件。"""
    return task_service.get_task(p, task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, p=Depends(identity)):
    """请求取消排队、执行或待审核中的任务。"""
    return task_service.cancel_task(p, task_id)


@router.post("/tasks/{task_id}/steer")
def steer(task_id: str, payload: SteerInput, p=Depends(identity)):
    """向进行中的协同调研任务下发幂等动态干预指令。"""
    return task_service.steer_task(p, task_id, text=payload.input, request_id=payload.request_id)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, p=Depends(identity)):
    """递增执行版本并重新入队重试失败或已取消任务。"""
    return task_service.retry_task(p, task_id)


@router.post("/tasks/{task_id}/review")
def review(task_id: str, payload: Decision, p=Depends(identity)):
    """人工审核待确认的业务答案。"""
    return task_service.review_task(p, task_id, approve=payload.approve, comment=payload.comment)


@router.get("/tasks/{task_id}/events")
async def events(task_id: str, request: Request, after: int = Query(0, ge=0), p=Depends(identity)):
    """以 SSE 流推送任务执行阶段进展，并在终态后通知客户端回源拉取权限过滤结果。"""
    task_service.get_task(p, task_id)

    async def stream():
        cursor = after
        for _ in range(1200):
            if await request.is_disconnected():
                return
            try:
                current = identity(request)
                allowed, rows, terminal = task_service.poll_task_events(current, task_id, cursor)
                if not allowed:
                    return
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if terminal is not None:
                    yield f"event: done\ndata: {json.dumps(terminal, default=str, ensure_ascii=False)}\n\n"
                    return
            except Denied:
                return
            yield ": 心跳\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/tools")
def tool(payload: Tool, p=Depends(identity)):
    """前端直接调用的受控业务查询或文档检索工具端点。"""
    return task_service.execute_tool(p, name=payload.name, query=payload.query, identifier=payload.identifier)
