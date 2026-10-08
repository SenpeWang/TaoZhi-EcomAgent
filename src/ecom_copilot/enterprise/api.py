from __future__ import annotations
import asyncio,hashlib,json,secrets
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from fastapi import FastAPI,Request,Response,Depends,UploadFile,File,Form,Header,Query
from fastapi.responses import JSONResponse,FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .config import load_config,ROOT
from .db import database_connection,fetch_one,fetch_all,as_jsonb
from .policy import (Denied, audit, can_download, can_read, can_review, can_task, can_write, get_document, load_principal, require_document_read, require_system, sources_valid)
from .services import (approval, create_job, create_user, decide, edit_document, generate_id, invalidate_authorization, membership_request, new_document, present_job, public_user, user_request)
from .auth import identity,login,hash_token,SESSION_COOKIE,CSRF_COOKIE
from .passwords import hash_password,verify_password
from .privacy import redact

app=FastAPI(title="企业智能协作工作台",docs_url=None,redoc_url=None)
class Input(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
class Login(Input):
    username:str=Field(min_length=3,max_length=64)
    password:str=Field(min_length=1,max_length=128)
    tenant_code:str=""
class Password(Input):
    current_password:str=Field(max_length=128)
    new_password:str=Field(max_length=128)
class NewUser(Input):
    username:str=Field(min_length=3,max_length=64)
    password:str=Field(min_length=6,max_length=128)
    display_name:str=Field(default="",max_length=80)
class UserChange(Input):
    display_name:str|None=Field(default=None,max_length=80)
    active:bool|None=None
    system_admin:bool|None=None
    new_password:str|None=Field(default=None,max_length=128)
class Membership(Input):
    node_id:str
    role:Literal["employee","leader","boss"]
    remove:bool=False
class Org(Input):
    name:str=Field(min_length=1,max_length=80)
    kind:Literal["department","team"]
    parent_id:str
class OrgChange(Input):
    name:str|None=Field(default=None,min_length=1,max_length=80)
    parent_id:str|None=None
    active:bool|None=None
class Decision(Input):
    approve:bool
    comment:str=Field(default="",max_length=1000)
class Doc(Input):
    title:str=Field(min_length=1,max_length=160)
    body:str=Field(min_length=1,max_length=200000)
    node_id:str
    scope:Literal["company","org","selected"]="org"
    level:int=Field(default=2,ge=1,le=3)
class EditDoc(Input):
    title:str=Field(min_length=1,max_length=160)
    body:str=Field(min_length=1,max_length=200000)
    version:int=Field(ge=1)
class DocPolicy(Input):
    scope:Literal["company","org","selected"]|None=None
    node_id:str|None=None
    level:int|None=Field(default=None,ge=1,le=3)
    ai_allowed:bool|None=None
    download_allowed:bool|None=None
class Grant(Input):
    user_id:str
    effect:Literal["allow","deny"]="allow"
    can_download:bool=False
    expires_at:datetime|None=None
    revoke:bool=False
class Question(Input):
    question:str=Field(min_length=1,max_length=4000)
    node_id:str=""
    input_level:int=Field(default=1,ge=1,le=3)
    depth:Literal["quick","standard","deep"]="standard"
    require_review:bool=False
    allow_external:bool=True
    sku_id:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    order_id:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
class Tool(Input):
    name:Literal["search_knowledge","query_product_spec","query_compatibility","query_stock","query_order"]
    query:str=Field(default="",max_length=4000)
    identifier:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")

@app.middleware("http")
async def request_id(request,call_next):
    request.state.request_id=secrets.token_hex(8)
    response=await call_next(request)
    response.headers["X-Request-Id"]=request.state.request_id
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="same-origin"
    if request.url.path.startswith("/api/"):response.headers["Cache-Control"]="no-store"
    return response
@app.exception_handler(Denied)
async def denied(request,exc):
    return JSONResponse({"code":exc.code,"message":exc.message,"request_id":getattr(request.state,"request_id","")},status_code=exc.status)
@app.exception_handler(RequestValidationError)
async def invalid(request,exc):
    return JSONResponse({"code":"INVALID_INPUT","message":"输入格式不正确，请检查必填项、长度和数值范围","request_id":getattr(request.state,"request_id","")},status_code=422)
@app.exception_handler(Exception)
async def unexpected(request,exc):
    return JSONResponse({"code":"INTERNAL_ERROR","message":"服务暂时无法处理请求，请稍后重试","request_id":getattr(request.state,"request_id","")},status_code=500)
@app.get("/api/health")
@app.get("/api/health")
def health():
    with database_connection() as c:
        fetch_one(c,"SELECT 1")
        worker=fetch_one(c,"SELECT count(*) AS n FROM jobs WHERE state='running' AND lease_until>now()")
    return dict(status="ok",version="3.0",message="服务正常")
@app.get("/api/info")
def info():
    with database_connection() as c:t=fetch_one(c,"SELECT name FROM tenants WHERE code=%s",(load_config().tenant_code,))
    return dict(name=t["name"] if t else "企业智能协作工作台",mode=load_config().mode,demo=load_config().mode=="demo",tenant_code=load_config().tenant_code)
@app.post("/api/auth/login")
@app.post("/api/auth/login")
def signin(payload:Login,request:Request,response:Response):
    p,sid,csrf,expires=login(payload.username,payload.password,request.client.host if request.client else "local",payload.tenant_code or load_config().tenant_code)
    response.set_cookie(SESSION_COOKIE,sid,httponly=True,secure=load_config().cookie_secure,samesite="strict",max_age=7200,path="/")
    response.set_cookie(CSRF_COOKIE,csrf,httponly=False,secure=load_config().cookie_secure,samesite="strict",max_age=7200,path="/")
    return dict(user=p.public(),csrf_token=csrf,expires_at=expires,message="登录成功")
@app.get("/api/auth/me")
@app.get("/api/auth/me")
def me(p=Depends(identity)):return p.public()
@app.post("/api/auth/logout")
@app.post("/api/auth/logout")
def logout(request:Request,response:Response,p=Depends(identity)):
    with database_connection() as c:
        c.execute("UPDATE sessions SET revoked=true WHERE id=%s",(hash_token(request.cookies.get(SESSION_COOKIE,"")),));audit(c,p,"退出登录",system=True)
    response.delete_cookie(SESSION_COOKIE);response.delete_cookie(CSRF_COOKIE)
    return {"message":"已退出登录"}
@app.post("/api/auth/password")
def password(payload:Password,p=Depends(identity)):
    with database_connection() as c:
        row=fetch_one(c,"SELECT * FROM users WHERE id=%s FOR UPDATE",(p.id,))
        if not verify_password(payload.current_password,row["password_hash"]):raise Denied("原密码不正确","INVALID_PASSWORD",400)
        if verify_password(payload.new_password,row["password_hash"]):raise Denied("新密码不能与原密码相同","INVALID_PASSWORD",400)
        c.execute("UPDATE users SET password_hash=%s,must_change=false WHERE id=%s",(hash_password(payload.new_password,load_config().mode),p.id))
        invalidate_authorization(c,p.tenant_id,p.id);audit(c,p,"修改本人密码",p.id,system=True)
    for folder in (Path.home()/".local/share/ecom-copilot",Path.home()/".local/share/ecom-agent"):
        for private in folder.glob("bootstrap-*.json"):
            try:
                if json.loads(private.read_text()).get("user_id")==p.id:private.unlink()
            except (OSError,ValueError):pass
    return {"message":"密码已修改，请重新登录"}
@app.get("/api/organizations")
def organizations(p=Depends(identity)):return {"items":list(p.nodes.values())}
@app.post("/api/organizations",status_code=201)
def add_org(payload:Org,p=Depends(identity)):
    require_system(p)
    parent=p.nodes.get(payload.parent_id)
    if not parent or (payload.kind=="department" and parent["kind"]!="company") or (payload.kind=="team" and parent["kind"]!="department"):
        raise Denied("部门应属于公司，小组应属于部门","INVALID_ORGANIZATION",400)
    with database_connection() as c:
        row=fetch_one(c,"INSERT INTO org_nodes(id,tenant_id,parent_id,name,kind) VALUES(%s,%s,%s,%s,%s) RETURNING *",(generate_id("org"),p.tenant_id,payload.parent_id,payload.name,payload.kind))
        invalidate_authorization(c,p.tenant_id);audit(c,p,"新增组织",row["id"],system=True)
    return row
@app.patch("/api/organizations/{node_id}",status_code=202)
def update_org(node_id:str,payload:OrgChange,p=Depends(identity)):
    require_system(p);n=p.nodes.get(node_id)
    if not n or n["kind"]=="company":raise Denied("组织不存在或不能修改","INVALID",400)
    with database_connection() as c:return approval(c,p,"org",node_id,dict(**payload.model_dump(exclude_none=True),expected_version=n["version"]),p.root)
@app.get("/api/users")
def users(p=Depends(identity)):
    if not(p.system_admin or p.boss or p.leader):return {"items":[p.public()]}
    with database_connection() as c:
        rows=fetch_all(c,"SELECT * FROM users WHERE tenant_id=%s ORDER BY created_at",(p.tenant_id,))
        visible=[]
        for u in rows:
            membership=fetch_all(c,"SELECT node_id,role FROM memberships WHERE tenant_id=%s AND user_id=%s AND active",(p.tenant_id,u["id"]))
            if p.system_admin or p.boss or any(p.manages(m["node_id"]) for m in membership):
                visible.append(dict(**public_user(u),memberships=membership))
        return {"items":visible}
@app.post("/api/users",status_code=201)
def add_user(payload:NewUser,p=Depends(identity)):
    with database_connection() as c:return create_user(c,p,**payload.model_dump())
@app.patch("/api/users/{user_id}",status_code=202)
def change_user(user_id:str,payload:UserChange,p=Depends(identity)):
    with database_connection() as c:return user_request(c,p,user_id,payload.model_dump(exclude_none=True))
@app.post("/api/users/{user_id}/memberships",status_code=202)
def assign(user_id:str,payload:Membership,p=Depends(identity)):
    with database_connection() as c:return membership_request(c,p,user_id,payload.node_id,payload.role,payload.remove)
@app.get("/api/documents")
def documents(p=Depends(identity)):
    with database_connection() as c:
        rows=fetch_all(c,"SELECT * FROM documents WHERE tenant_id=%s AND state<>'deleted' ORDER BY updated_at DESC LIMIT 2000",(p.tenant_id,))
        result=[]
        for d in rows:
            if can_read(c,p,d,True):
                result.append(dict(**{k:d[k] for k in ("id","title","scope","node_id","level","state","current_version","acl_version","ai_allowed","download_allowed","updated_at","error")},
                   node_name=p.nodes.get(d["node_id"],{}).get("name",""),can_write=can_write(c,p,d),can_download=can_download(c,p,d),can_policy=p.boss or p.system_admin or p.manages(d["node_id"])))
        return {"items":result}
@app.post("/api/documents",status_code=201)
def add_document(payload:Doc,p=Depends(identity)):
    with database_connection() as c:return new_document(c,p,**payload.model_dump())
@app.post("/api/documents/upload",status_code=201)
async def upload(file:UploadFile=File(...),title:str=Form(...),node_id:str=Form(...),scope:str=Form("org"),level:int=Form(2),p=Depends(identity)):
    from .ingest import validate_upload
    content=await file.read(20*1024*1024+1)
    mime=validate_upload(file.filename or "",content)
    try:parsed=Doc(title=title,body="待解析附件",node_id=node_id,scope=scope,level=level)
    except ValidationError:raise Denied("资料名称、范围或密级格式不正确","INVALID_INPUT",422) from None
    name=generate_id("blob")+"."+{"application/pdf":"pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document":"docx","text/plain":"txt"}[mime]
    folder=load_config().private_dir/p.tenant_id;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/name;path.write_bytes(content);path.chmod(0o600)
    try:
        with database_connection() as c:return new_document(c,p,**parsed.model_dump(),blob=name,mime=mime)
    except Exception:path.unlink(missing_ok=True);raise
@app.get("/api/documents/{doc_id}")
def get_doc(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id);require_document_read(c,p,d)
        v=fetch_one(c,"SELECT body FROM document_versions WHERE document_id=%s AND version=%s",(d["id"],d["current_version"]))
        return dict(**{k:d[k] for k in ("id","title","scope","node_id","level","state","current_version","acl_version","ai_allowed","download_allowed","error")},body=v["body"] if p.boss else redact(v["body"]),can_write=can_write(c,p,d) and (p.boss or v["body"]==redact(v["body"])),can_download=can_download(c,p,d))
@app.get("/api/documents/{doc_id}/versions")
def versions(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id);require_document_read(c,p,d)
        result=[]
        for v in fetch_all(c,"SELECT * FROM document_versions WHERE tenant_id=%s AND document_id=%s ORDER BY version DESC",(p.tenant_id,d["id"])):
            historic={**d,**v["policy"]}
            if (v["version"]==d["current_version"] and can_read(c,p,d,True)) or can_read(c,p,historic,True):
                item={k:v[k] for k in ("version","title","body","created_at","content_hash")}
                if not p.boss:item["body"]=redact(item["body"])
                result.append(item)
        return {"items":result}
@app.put("/api/documents/{doc_id}")
def edit(doc_id:str,payload:EditDoc,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id);return edit_document(c,p,d,**payload.model_dump())
@app.delete("/api/documents/{doc_id}")
def remove(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        if not can_write(c,p,d):raise Denied("没有资料删除权限")
        c.execute("UPDATE documents SET state='deleted',acl_version=acl_version+1,updated_at=now() WHERE id=%s",(d["id"],))
        invalidate_authorization(c,p.tenant_id);audit(c,p,"删除资料",d["id"],d["node_id"],d["level"])
    return {"message":"资料已删除，历史来源不再可用"}
@app.post("/api/documents/{doc_id}/policy",status_code=202)
def policy_change(doc_id:str,payload:DocPolicy,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        if not (p.boss or p.system_admin or p.manages(d["node_id"])):raise Denied()
        data=payload.model_dump(exclude_none=True)
        if data.get("node_id",d["node_id"]) not in p.nodes:raise Denied("组织无效","INVALID",400)
        scope=data.get("scope",d["scope"]);node=data.get("node_id",d["node_id"])
        if scope=="company" and node!=p.root:raise Denied("公司共享资料应归属公司","INVALID",400)
        return approval(c,p,"document_policy",d["id"],dict(**data,expected_acl=d["acl_version"],expected_version=d["current_version"]),d["node_id"])
@app.get("/api/documents/{doc_id}/permission-status")
def permission_status(doc_id:str,p=Depends(identity)):
    require_system(p)
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        audit(c,p,"查询资料授权配置",d["id"],system=True)
        return {k:d[k] for k in ("id","scope","node_id","level","state","current_version","acl_version","ai_allowed","download_allowed")}

@app.get("/api/system/status")
def system_status(p=Depends(identity)):
    require_system(p)
    with database_connection() as c:
        counts=fetch_all(c,"SELECT state,count(*) AS count FROM jobs WHERE tenant_id=%s GROUP BY state",(p.tenant_id,))
        workers=fetch_one(c,"SELECT count(*) AS count FROM worker_heartbeats WHERE updated_at>now()-interval '30 seconds'")["count"]
    from ..config import get_settings
    s=get_settings()
    from .embedding import available as embed_available
    return dict(database=True,worker_available=workers>0,counts=counts,model_configured=bool(s.api_key),business_configured=bool(s.business_api_url and s.business_api_token),semantic_retrieval=embed_available(),mode=load_config().mode)

@app.get("/api/documents/{doc_id}/grants")
def grants(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        if not (p.boss or p.system_admin or p.manages(d["node_id"])):raise Denied()
        return {"items":fetch_all(c,"SELECT g.user_id,u.display_name,g.effect,g.can_download,g.can_write,g.expires_at FROM document_grants g JOIN users u ON u.id=g.user_id WHERE g.tenant_id=%s AND g.document_id=%s",(p.tenant_id,d["id"]))}
@app.post("/api/documents/{doc_id}/grants",status_code=202)
def grant_change(doc_id:str,payload:Grant,p=Depends(identity)):
    if payload.expires_at and (payload.expires_at.tzinfo is None or payload.expires_at<=datetime.now(timezone.utc)):raise Denied("授权到期时间须为未来时间","INVALID_EXPIRY",400)
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        if not (p.boss or p.system_admin or p.manages(d["node_id"])):raise Denied()
        if not fetch_one(c,"SELECT id FROM users WHERE tenant_id=%s AND id=%s AND active",(p.tenant_id,payload.user_id)):raise Denied("授权人员无效","INVALID",400)
        return approval(c,p,"grant",d["id"],dict(**payload.model_dump(mode="json"),expected_acl=d["acl_version"],expected_version=d["current_version"]),d["node_id"])
@app.post("/api/documents/{doc_id}/request-access",status_code=202)
def request_access(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=fetch_one(c,"SELECT * FROM documents WHERE tenant_id=%s AND id=%s AND state='published'",(p.tenant_id,doc_id))
        if d:approval(c,p,"grant",doc_id,dict(user_id=p.id,can_download=False,can_write=False,effect="allow",expected_acl=d["acl_version"],expected_version=d["current_version"]),d["node_id"])
    return {"message":"申请已提交，资料负责人确认后可开放访问"}
@app.get("/api/documents/{doc_id}/download")
def download(doc_id:str,p=Depends(identity)):
    with database_connection() as c:
        d=get_document(c,p,doc_id)
        if not can_download(c,p,d):raise Denied("没有资料下载权限","NOT_FOUND",404)
        v=fetch_one(c,"SELECT * FROM document_versions WHERE document_id=%s AND version=%s",(d["id"],d["current_version"]))
        audit(c,p,"下载资料",d["id"],d["node_id"],d["level"])
        if v["blob_name"]:
            if not p.boss:
                return Response(redact(v["body"]),media_type="text/plain; charset=utf-8",headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(d["title"]+".txt")})
            path=load_config().private_dir/p.tenant_id/v["blob_name"]
            if not path.is_file():raise Denied("附件不可用","NOT_FOUND",404)
            return FileResponse(path,media_type=v["mime_type"],filename=d["title"]+path.suffix)
        return Response(v["body"] if p.boss else redact(v["body"]),media_type="text/plain; charset=utf-8",headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(d["title"]+".txt")})
@app.get("/api/approvals")
def approvals(p=Depends(identity)):
    with database_connection() as c:
        result=[]
        for a in fetch_all(c,"SELECT * FROM approvals WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 500",(p.tenant_id,)):
            responsible=p.boss or (a["required_role"]=="leader" and p.manages(a["node_id"]) and p.id!=a["requested_by"])
            if not (responsible or a["requested_by"]==p.id or (p.system_admin and a["kind"] in ("membership","user","org"))):continue
            label={"membership":"组织任职","user":"账号变更","document_policy":"资料权限","grant":"单文档授权","publish":"资料发布","org":"组织调整"}.get(a["kind"],"业务申请")
            d=fetch_one(c,"SELECT * FROM documents WHERE tenant_id=%s AND id=%s",(p.tenant_id,a["target_id"]))
            title=d["title"] if d and can_read(c,p,d,True) else ""
            payload={k:v for k,v in a["payload"].items() if k not in ("password_hash",)}
            if "password_hash" in a["payload"]:payload["reset_password"]=True
            result.append(dict(**{k:a[k] for k in ("id","kind","target_id","requested_by","required_role","state","created_at","comment")},label=label,title=title,can_decide=responsible,payload=payload))
        return {"items":result}
@app.post("/api/approvals/{aid}/decision")
def decision(aid:str,payload:Decision,p=Depends(identity)):
    with database_connection() as c:return decide(c,p,aid,payload.approve,payload.comment)
@app.post("/api/research/task",status_code=202)
@app.post("/api/tasks",status_code=202)
def ask(payload:Question,idempotency_key:str|None=Header(default=None,max_length=128),p=Depends(identity)):
    node=payload.node_id or next((m["node_id"] for m in p.memberships if m["role"] in ("leader","employee")),p.root)
    data=payload.model_dump(exclude={"question","node_id","input_level"})
    if payload.input_level>=3 and not p.boss:data["allow_external"]=False
    with database_connection() as c:
        job=create_job(c,p,"ask",node,data,payload.question,payload.input_level,idempotency_key)
        return {"id":job["id"],"task_id":job["id"],"state":job["state"],"message":"任务已提交"}
@app.get("/api/research/tasks")
@app.get("/api/tasks")
def tasks(p=Depends(identity)):
    with database_connection() as c:
        rows=fetch_all(c,"SELECT * FROM jobs WHERE tenant_id=%s AND kind='ask' ORDER BY created_at DESC LIMIT 500",(p.tenant_id,))
        return {"items":[present_job(c,p,r,False) for r in rows if can_task(p,r) and (r["owner_id"]==p.id or sources_valid(c,p,r["sources"]))]}
@app.get("/api/research/task/{task_id}")
@app.get("/api/tasks/{task_id}")
def task(task_id:str,p=Depends(identity)):
    with database_connection() as c:
        r=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s",(p.tenant_id,task_id))
        if not r:raise Denied("任务不存在或没有访问权限","NOT_FOUND",404)
        return present_job(c,p,r)
@app.post("/api/tasks/{task_id}/cancel")
def cancel(task_id:str,p=Depends(identity)):
    with database_connection() as c:
        r=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,task_id))
        if not r or not (r["owner_id"]==p.id or p.boss):raise Denied()
        if r["state"] not in ("queued","running","review"):raise Denied("任务已经结束","CONFLICT",409)
        c.execute("UPDATE jobs SET cancel_requested=true,state=CASE WHEN state='running' THEN state ELSE 'cancelled' END WHERE id=%s",(task_id,))
        audit(c,p,"取消任务",task_id,r["node_id"],r["input_level"])
    return {"message":"已请求取消"}
@app.post("/api/tasks/{task_id}/review")
def review(task_id:str,payload:Decision,p=Depends(identity)):
    with database_connection() as c:
        r=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,task_id))
        if not r or r["state"]!="review":raise Denied("任务不在待审核状态","CONFLICT",409)
        if not can_review(p,r) or (r["owner_id"]==p.id and not p.boss):raise Denied("需要对应范围的另一位负责人审核")
        owner=load_principal(c,r["owner_id"])
        if not sources_valid(c,p,r["sources"]) or not sources_valid(c,owner,r["sources"]):raise Denied("来源或权限已变化，请重新查询","STALE",409)
        if payload.approve and (r["result"] or {}).get("hard_block"):raise Denied("缺少可靠来源，不能审核为事实","QUALITY_BLOCK",409)
        c.execute("UPDATE jobs SET state=%s,finished_at=now(),progress=100,stage=%s WHERE id=%s",("completed" if payload.approve else "rejected","已发布" if payload.approve else "审核未通过",r["id"]))
        c.execute("INSERT INTO task_reviews(tenant_id,job_id,reviewer_id,decision,comment) VALUES(%s,%s,%s,%s,%s)",(p.tenant_id,r["id"],p.id,"approved" if payload.approve else "rejected",payload.comment))
        audit(c,p,"审核业务答案",r["id"],r["node_id"],r["input_level"],detail={"decision":"approved" if payload.approve else "rejected"})
    return {"message":"答案已发布" if payload.approve else "答案未通过审核"}
@app.get("/api/tasks/{task_id}/events")
async def events(task_id:str,request:Request,after:int=Query(0,ge=0),p=Depends(identity)):
    task(task_id,p)
    async def stream():
        cursor=after
        for _ in range(1200):
            if await request.is_disconnected():return
            try:
                current=identity(request)
                with database_connection() as c:
                    job=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s",(current.tenant_id,task_id))
                    if not job or not can_task(current,job):return
                    rows=fetch_all(c,"SELECT id,stage,progress,status FROM task_events WHERE tenant_id=%s AND job_id=%s AND id>%s ORDER BY id",(current.tenant_id,task_id,cursor))
                    for row in rows:
                        cursor=row["id"];yield "id: "+str(cursor)+"\ndata: "+json.dumps(row,ensure_ascii=False)+"\n\n"
                    if job["state"] in ("completed","review","failed","cancelled","rejected"):
                        # 终态只推摘要；正文与引用由客户端经 GET /tasks/{id} 另行获取，
                        # 该请求走完整权限与来源有效性判定，事件流不承载问题原文与证据
                        yield "event: done\ndata: "+json.dumps({k:job[k] for k in ("id","state","stage","progress","error")},default=str,ensure_ascii=False)+"\n\n"
                        return
            except Denied:return
            yield ": 心跳\n\n";await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type="text/event-stream")
@app.post("/api/tools")
def tool(payload:Tool,p=Depends(identity)):
    with database_connection() as c:
        if payload.name in ("query_stock","query_order"):
            from .business import lookup
            return lookup(c,p,"stock" if payload.name=="query_stock" else "orders",payload.identifier)
        from .retriever import search_documents
        hits=search_documents(c,p,payload.query,8)
        return {"items":hits}
@app.get("/api/knowledge-map")
def knowledge_map(p=Depends(identity)):
    from .facts import list_visible_mentions
    with database_connection() as c:return {"items":list_visible_mentions(c,p)}

@app.get("/api/audit")
def audit_list(p=Depends(identity)):
    if not(p.system_admin or p.boss or p.leader):raise Denied()
    with database_connection() as c:
        rows=fetch_all(c,"SELECT * FROM audit_events WHERE tenant_id=%s ORDER BY id DESC LIMIT 500",(p.tenant_id,))
        return {"items":[r for r in rows if (r["system"] and (p.system_admin or p.boss)) or (not r["system"] and p.grade(r["node_id"])>=r["level"] and p.manages(r["node_id"]))]}
@app.get("/api/dashboard")
def dashboard(p=Depends(identity)):
    with database_connection() as c:
        docs=documents(p)["items"]
        jobs=fetch_all(c,"SELECT * FROM jobs WHERE tenant_id=%s AND kind='ask'",(p.tenant_id,))
        visible=[r for r in jobs if can_task(p,r) and sources_valid(c,p,r["sources"])]
        from ..config import get_settings
        settings=get_settings()
        return dict(documents=len(docs),tasks=len(visible),review=sum(r["state"]=="review" for r in visible),completed=sum(r["state"]=="completed" for r in visible),
          teams=[p.nodes[m["node_id"]]["name"] for m in p.memberships if m["role"]!="boss"],
          model_ready=bool(settings.api_key),business_ready=bool(settings.business_api_url and settings.business_api_token),mode=load_config().mode)

dist=ROOT/"web/dist"
if (dist/"assets").exists():app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
@app.get("/{path:path}",include_in_schema=False)
def spa(path:str):
    if path.startswith("api/"):raise Denied("接口不存在","NOT_FOUND",404)
    if not (dist/"index.html").exists():return Response("中文工作台正在准备",media_type="text/plain; charset=utf-8",status_code=503)
    return FileResponse(dist/"index.html",headers={"Cache-Control":"no-cache"})
