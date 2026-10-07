"""Authenticated task API: owner isolation, durable jobs, review and SSE replay."""
from __future__ import annotations
import asyncio
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from ...schemas.common import PermissionContext
from ...schemas.research import ResearchDepth, ResearchTask
from ...security.auth import User, require_permission, identity
from ..sse import sse_response
from ..tasks import get_task_manager, AdmissionError, IdempotencyConflict

router=APIRouter(prefix="/api/research",tags=["research"])
class CreateTaskRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    question:str=Field(min_length=1,max_length=4000)
    depth:ResearchDepth=ResearchDepth.STANDARD
    user_id:str=""
    org_tag:str=""
    category:str=Field(default="",max_length=100)
    product_line:str=Field(default="",max_length=100)
    brand:str=Field(default="",max_length=100)
    sku_id: str = Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    order_id: str = Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    max_sources:int=Field(default=12,ge=1,le=30)
    require_human_review:bool=False

class CreateTaskResponse(BaseModel):
    task_id:str
    status:str
    estimated_time:int
    stream_url:str

def _owned(task_id,user):
    record=get_task_manager().get(task_id)
    if record is None or (not user.is_admin and
       (record.task.org_tag != user.org_tag or
        (record.task.user_id != user.user_id and not user.has("research:review")))):
        raise HTTPException(status_code=404,detail="任务不存在")
    return record

_SAFE_EVENTS={"stage","event_id","progress","status","elapsed_s","task_id"}
def _event(event):
    return {k:v for k,v in event.items() if k in _SAFE_EVENTS}
def _present(record,user):
    from ...security.access import get_access_store
    result=record.result
    restricted=False
    if result:
        sources=result.get("protected_sources")
        if sources is None:
            restricted=not user.has("research:review")  # Untracked legacy reports are management only.
        elif sources:
            restricted=any(not get_access_store().can_read(x,user.context()) for x in sources)
    if restricted:
        result={"report":None,"publication":"permission_changed","message":"资料权限已变更，当前账号不能读取旧结果，请重新提问"}
    elif record.task.status.value=="awaiting_review" and not user.has("research:review"):
        result={"quality":(result or {}).get("quality",{}),"report":None,"publication":"awaiting_review"}
    elif result and not user.has("research:review"):
        # Internal hypotheses/traces can contain fragments beyond the published answer.
        result={k:result.get(k) for k in ("report","quality","metrics")}
    return {**record.to_dict(),"result":result,"events":[_event(e) for e in record.events[-20:]]}

def _submit(payload,user,key):
    uid,org=identity(user,payload.user_id,payload.org_tag)
    task=ResearchTask(**payload.model_dump(exclude={"user_id","org_tag"}),user_id=uid,org_tag=org)
    permission=PermissionContext(user_id=uid,org_tag=org,roles=user.roles,is_admin=user.has("admin:all"))
    try:
        return get_task_manager().submit(task,permission,key)
    except AdmissionError as e:raise HTTPException(status_code=429,detail=str(e),headers={"Retry-After":"5"})
    except IdempotencyConflict as e:raise HTTPException(status_code=409,detail=str(e))

@router.post("/task",response_model=CreateTaskResponse,status_code=202)
async def create_task(payload:CreateTaskRequest,user:User=Depends(require_permission("research:ask")),
                      idempotency_key:str|None=Header(default=None,max_length=128)):
    r=_submit(payload,user,idempotency_key)
    return CreateTaskResponse(task_id=r.task.task_id,status=r.task.status.value,
                              estimated_time=r.task.estimated_time,stream_url=f"/api/research/task/{r.task.task_id}/stream")

@router.get("/task/{task_id}")
async def get_task(task_id:str,user:User=Depends(require_permission("research:read"))):
    r=_owned(task_id,user)
    return _present(r,user)

@router.get("/task/{task_id}/stream")
async def stream_task(task_id:str,user:User=Depends(require_permission("research:read")),
                      last_event_id:int=Header(default=0,ge=0)):
    _owned(task_id,user)
    async def safe_stream():
        from ...security.accounts import get_account_store
        async for event in get_task_manager().event_stream(task_id,after=last_event_id):
            if user.session_id and not get_account_store().session_user(user.session_id,user.user_id):
                yield {"stage":"error","error":"登录已失效，请重新登录"}
                return
            yield _event(event)
    return sse_response(safe_stream())

class ReviewRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    decision:Literal["approve","reject"]
    comment:str=Field(default="",max_length=2000)

@router.post("/task/{task_id}/review",status_code=202)
async def review_task(task_id:str,payload:ReviewRequest,user:User=Depends(require_permission("research:review"))):
    record=_owned(task_id,user)
    if payload.decision=="approve" and ((_present(record,user).get("result") or {}).get("publication")=="permission_changed"):
        raise HTTPException(409,"所用资料已删除或权限变更，请重新核验后再发布")
    try:r=get_task_manager().review(task_id,payload.decision,payload.comment,user.user_id)
    except ValueError as e:raise HTTPException(status_code=409,detail=str(e))
    return r.to_dict()

@router.post("/task/{task_id}/cancel")
async def cancel_task(task_id:str,user:User=Depends(require_permission("research:ask"))):
    _owned(task_id,user)
    try:return get_task_manager().cancel(task_id).to_dict()
    except ValueError as e:raise HTTPException(status_code=409,detail=str(e))

@router.get("/tasks")
async def list_tasks(limit:int=Query(default=50,ge=1,le=200),user:User=Depends(require_permission("research:read"))):
    return get_task_manager().list_tasks(limit,user)

@router.post("/sync")
async def run_sync(payload:CreateTaskRequest,user:User=Depends(require_permission("research:ask")),
                   idempotency_key:str|None=Header(default=None,max_length=128)):
    r=_submit(payload,user,idempotency_key)
    task_id=r.task.task_id
    for _ in range(600):
        r=get_task_manager().get(task_id)
        if r.finished:
            out=_present(r,user)
            return {"task_id":task_id,"status":out["status"],"result":out["result"]}
        await asyncio.sleep(0.2)
    raise HTTPException(status_code=504,detail={"message":"任务仍在后台执行，请轮询","task_id":task_id})
