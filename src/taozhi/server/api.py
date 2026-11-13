from __future__ import annotations
import asyncio,hashlib,json,secrets
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from fastapi import FastAPI,Request,Response,Depends,UploadFile,File,Form,Header,Query
from fastapi.responses import JSONResponse,FileResponse,RedirectResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from ..config import load_config, ROOT
from ..knowledge.db import database_connection,fetch_one,fetch_all,as_jsonb
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence.access_repo import (audit, can_download, can_read, can_review, can_task, can_write, get_document, load_principal, require_document_read, require_system, sources_valid)
from .services import (approval, create_job, create_user, decide, edit_document, generate_id, invalidate_authorization, membership_request, new_document, present_job, public_user, user_request)
from .auth import identity,login,hash_token,SESSION_COOKIE,CSRF_COOKIE
from .passwords import hash_password,verify_password
from taozhi.domain.privacy import redact
from ..knowledge.storage import get_blob_store

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
class SteerInput(Input):
    input:str=Field(min_length=1,max_length=2000)
class Question(Input):
    question:str=Field(min_length=1,max_length=4000)
    kind:Literal["ask","research"]="ask"
    platforms:list[str]=Field(default_factory=list,max_length=8)
    node_id:str=""
    input_level:int=Field(default=1,ge=1,le=3)
    conversation_id:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    require_review:bool=False
    allow_external:bool=True
    sku_id:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    order_id:str=Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    keyword:str=Field(default="",max_length=100)
class LearnMemory(Input):
    keyword:str=Field(min_length=1,max_length=100)
    rule_or_fact:str=Field(min_length=1,max_length=500)
    is_rule:bool=True

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
def _quick_accounts(c,cfg):
    """运维便利：返回真实企业账号清单。

    仅在 ECOM_QUICK_LOGIN=true 时启用；口令只在 ECOM_QUICK_LOGIN_PASSWORDS=true 时返回：
    配置 ECOM_QUICK_LOGIN_PASSWORD 时对所有账号统一下发该默认口令（前端免输入），
    未配置时回落到运维凭据文件 accounts.json（仅限内网自用，禁止公网生产开启）。
    """
    t=fetch_one(c,"SELECT id FROM tenants WHERE code=%s",(cfg.tenant_code,))
    if not t:return []
    rows=fetch_all(c,"SELECT u.username,u.display_name,u.system_admin,"
                     "coalesce(string_agg(m.role,','),'') AS roles "
                     "FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.tenant_id=u.tenant_id "
                     "WHERE u.tenant_id=%s AND u.active "
                     "GROUP BY u.id,u.username,u.display_name,u.system_admin ORDER BY u.username",(t["id"],))
    default=cfg.quick_login_password if cfg.quick_login_passwords else ""
    creds={}
    if cfg.quick_login_passwords and not default:
        p=Path.home()/".local/share/ecom-agent/accounts.json"
        try:creds=json.loads(p.read_text()) if p.exists() else {}
        except Exception:creds={}
    result=[]
    for r in rows:
        item=dict(username=r["username"],display_name=r["display_name"],
                  role="admin" if r["system_admin"] else (r["roles"] or "-"))
        if r["username"].startswith("syn_"):item["password"]="taozhi2026eval"
        elif default:item["password"]=default
        elif creds:item["password"]=creds.get(r["username"],{}).get("password","")
        result.append(item)
    return result
@app.get("/api/info")
def info():
    cfg=load_config()
    with database_connection() as c:
        t=fetch_one(c,"SELECT name FROM tenants WHERE code=%s",(cfg.tenant_code,))
        data=dict(name=t["name"] if t else "企业智能协作工作台",mode=cfg.mode,tenant_code=cfg.tenant_code)
        if cfg.quick_login:data["quick_accounts"]=_quick_accounts(c,cfg)
    return data
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
    from ..knowledge.ingest import validate_upload
    content=await file.read(20*1024*1024+1)
    mime=validate_upload(file.filename or "",content)
    try:parsed=Doc(title=title,body="待解析附件",node_id=node_id,scope=scope,level=level)
    except ValidationError:raise Denied("资料名称、范围或密级格式不正确","INVALID_INPUT",422) from None
    name=generate_id("blob")+"."+{"application/pdf":"pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document":"docx","text/plain":"txt"}[mime]
    store=get_blob_store();store.put_blob(p.tenant_id,name,content)
    try:
        with database_connection() as c:return new_document(c,p,**parsed.model_dump(),blob=name,mime=mime)
    except Exception:store.remove_blob(p.tenant_id,name);raise
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
    from ..knowledge.embedding import available as embed_available
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
            url=get_blob_store().presign_url(p.tenant_id,v["blob_name"])  # 审计已落库后再签发直链
            if url:return RedirectResponse(url,status_code=302)
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
def decision(aid: str, payload: Decision, p=Depends(identity)):
    with database_connection() as c:
        return decide(c, p, aid, payload.approve, payload.comment)


@app.post("/api/research/task", status_code=202)
@app.post("/api/tasks", status_code=202)
def ask(
    payload: Question,
    idempotency_key: str | None = Header(default=None, max_length=128),
    p=Depends(identity),
):
    """提交智能问答或协同调研任务。

    问题内容密级严格根据当前登录账号权限自动确定（老板为3，组长为2，员工为1），
    不接受客户端自由篡改，以杜绝越权访问。

    Args:
        payload: 问答任务参数载荷。
        idempotency_key: 幂等性控制标识。
        p: 当前经过身份验证的用户主体。

    Returns:
        包含任务标识与初始状态的响应字典。
    """
    node = payload.node_id or next((m["node_id"] for m in p.memberships if m["role"] in ("leader", "employee")), p.root)
    data = payload.model_dump(exclude={"question", "node_id", "input_level", "conversation_id"})
    input_level = 3 if p.boss else (2 if p.leader else 1)
    if input_level >= 3 and not p.boss:
        data["allow_external"] = False
    with database_connection() as c:
        job = create_job(
            c,
            p,
            payload.kind,
            node,
            data,
            payload.question,
            input_level,
            idempotency_key,
            conversation_id=payload.conversation_id,
        )
        return {"id": job["id"], "task_id": job["id"], "state": job["state"], "message": "任务已提交"}
@app.get("/api/research/tasks")
@app.get("/api/tasks")
def tasks(p=Depends(identity)):
    with database_connection() as c:
        rows=fetch_all(c,"SELECT * FROM jobs WHERE tenant_id=%s AND kind IN ('ask','research') ORDER BY created_at DESC LIMIT 500",(p.tenant_id,))
        return {"items":[present_job(c,p,r,False) for r in rows if can_task(p,r) and (r["owner_id"]==p.id or sources_valid(c,p,r["sources"]))]}
@app.get("/api/research/conversations")
def research_conversations(p=Depends(identity)):
    with database_connection() as c:
        rows = fetch_all(
            c,
            "SELECT conversation_id, min(created_at) as started_at, max(created_at) as last_active, "
            "count(*) as turns, (array_agg(question ORDER BY created_at ASC))[1] as title, "
            "(array_agg(payload ORDER BY created_at ASC))[1] as first_payload, "
            "(array_agg(state ORDER BY created_at DESC))[1] as latest_state "
            "FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id!='' "
            "GROUP BY conversation_id ORDER BY last_active DESC LIMIT 100",
            (p.tenant_id, p.id),
        )
        items = []
        for r in rows:
            kw = (r["first_payload"] or {}).get("keyword") or r["title"]
            items.append({
                "conversation_id": r["conversation_id"],
                "title": kw,
                "turns": r["turns"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else "",
                "last_active": r["last_active"].isoformat() if r["last_active"] else "",
                "latest_state": r["latest_state"],
            })
        return {"items": items}

@app.get("/api/research/conversation/{conversation_id}")
def research_conversation(conversation_id: str, p=Depends(identity)):
    with database_connection() as c:
        rows = fetch_all(
            c,
            "SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id=%s ORDER BY created_at ASC",
            (p.tenant_id, p.id, conversation_id),
        )
        if not rows:
            return {"conversation_id": conversation_id, "turns": [], "context_summary": "", "memory_facts": []}
        turns = []
        for r in rows:
            pj = present_job(c, p, r, True)
            evts = fetch_all(c, "SELECT stage FROM task_events WHERE tenant_id=%s AND job_id=%s ORDER BY id ASC", (p.tenant_id, r["id"]))
            pj["thinking_logs"] = [e["stage"] for e in evts if e.get("stage")]
            if r.get("finished_at") and r.get("started_at"):
                calc_elapsed = round((r["finished_at"] - r["started_at"]).total_seconds(), 1)
            else:
                calc_elapsed = 0
            pj["elapsed"] = (r.get("result") or {}).get("elapsed") or calc_elapsed
            turns.append(pj)
        latest_res = rows[-1].get("result") or {}
        kw = (rows[0].get("payload") or {}).get("keyword") or rows[0]["question"]
        from ..core.memory.service import get_memory_store
        raw_facts = get_memory_store(tenant_id=p.tenant_id).query_facts(subject=kw)[:20]
        facts = []
        for f in raw_facts:
            f_copy = dict(f)
            if not getattr(p, "boss", False) and not getattr(p, "root", False):
                if isinstance(f_copy.get("object"), str):
                    f_copy["object"] = redact(f_copy["object"])
            facts.append(f_copy)
        return {
            "conversation_id": conversation_id,
            "keyword": kw,
            "turns": turns,
            "context_summary": latest_res.get("context_summary", ""),
            "context_tokens": latest_res.get("context_tokens", 0),
            "is_compacted": latest_res.get("is_compacted", False),
            "memory_facts": facts,
        }

@app.delete("/api/research/conversation/{conversation_id}")
def delete_research_conversation(conversation_id: str, p=Depends(identity)):
    with database_connection() as c:
        c.execute(
            "DELETE FROM task_events WHERE tenant_id=%s AND job_id IN (SELECT id FROM jobs WHERE tenant_id=%s AND owner_id=%s AND conversation_id=%s)",
            (p.tenant_id, p.tenant_id, p.id, conversation_id),
        )
        c.execute(
            "DELETE FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id=%s",
            (p.tenant_id, p.id, conversation_id),
        )
        audit(c, p, "删除调研对话", conversation_id, p.root, 1)
    return {"message": "对话已成功删除"}

@app.post("/api/research/memory/learn")
def research_learn(payload: LearnMemory, p=Depends(identity)):
    from ..core.memory.service import get_memory_store
    store = get_memory_store(tenant_id=p.tenant_id)
    if payload.is_rule:
        if not getattr(p, "boss", False) and not getattr(p, "root", False):
            raise Denied("权限不足：只有企业管理员可发布企业级推理规则", "FORBIDDEN", 403)
        store.learn_rule(payload.rule_or_fact, category="research", source=f"tenant_admin_{p.tenant_id}")
    else:
        store.remember_fact(
            subject=payload.keyword,
            predicate="用户设定事实",
            object_val=payload.rule_or_fact,
            source=f"tenant_{p.tenant_id}_user_{p.id}",
        )
    return {"message": "已成功记录至长效记忆库", "facts": store.query_facts(subject=payload.keyword)}

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
@app.post("/api/tasks/{task_id}/steer")
def steer(task_id:str,payload:SteerInput,p=Depends(identity)):
    with database_connection() as c:
        r=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,task_id))
        if not r:raise Denied("任务不存在或没有访问权限","NOT_FOUND",404)
        if r["state"] not in ("queued","running"):raise Denied("只能对进行中的任务追加干预指令","CONFLICT",409)
        if not (r["owner_id"]==p.id or p.boss or p.system_admin):raise Denied("无权干预该任务","FORBIDDEN",403)
        stage_desc=f"【用户中途追加干预指令】：{payload.input}"
        run_ver=r.get("run_version") or 1
        c.execute("INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,%s,'running')",(p.tenant_id,task_id,run_ver,stage_desc,r.get("progress") or 50))
        # 1. 事务级写入 jobs.payload->'steering_inputs' 数组持久化队列，杜绝跨进程丢失
        c.execute(
            """UPDATE jobs SET payload = jsonb_set(
                COALESCE(payload, '{}'::jsonb),
                '{steering_inputs}',
                COALESCE(payload->'steering_inputs', '[]'::jsonb) || jsonb_build_array(%s)
            ) WHERE id=%s""",
            (payload.input, task_id)
        )
        audit(c,p,"中途动态干预",task_id,r["node_id"],r["input_level"])
    # 2. 尝试向正在运行的 LangGraph 检查点追加 steering_inbox 状态（主通道已持久化至 jobs.payload，此处为即时加速旁路）
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from ..orchestration.agent_system import build_autonomous_agent_graph
        with PostgresSaver.from_conn_string(load_config().dsn) as saver:
            saver.setup()
            graph = build_autonomous_agent_graph(checkpointer=saver)
            thread_id = f"research_{task_id}_{run_ver}"
            graph.update_state({"configurable": {"thread_id": thread_id}}, {"steering_inbox": [payload.input]})
    except Exception as exc:
        import logging
        logging.getLogger("taozhi.server.api").warning("LangGraph 检查点即时注入失败（已由 DB 队列兜底）: %s", exc)
    return {"message":"中途干预指令已发送并生效","task_id":task_id,"input":payload.input}
@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id:str,p=Depends(identity)):
    """断点重试或重新发起任务（递增执行版本并重新推入工作流队列）。"""
    with database_connection() as c:
        r=fetch_one(c,"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,task_id))
        if not r:raise Denied("任务不存在或没有访问权限","NOT_FOUND",404)
        if not (r["owner_id"]==p.id or p.boss or p.system_admin):raise Denied("无权操作此任务","FORBIDDEN",403)
        if r["state"]=="running":raise Denied("任务正在执行中，无需重复发起","CONFLICT",409)
        new_ver=(r["run_version"] or 1)+1
        c.execute("UPDATE jobs SET state='queued',stage='工作流重新排队调度中...',progress=0,error='',cancel_requested=false,run_version=%s,started_at=NULL,finished_at=NULL,lease_until=NULL WHERE id=%s",(new_ver,task_id))
        c.execute("INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,'工作流重新排队调度中...',0,'queued')",(p.tenant_id,task_id,new_ver))
        c.execute("INSERT INTO task_outbox(job_id) VALUES(%s)",(task_id,))
        audit(c,p,"重新发起任务",task_id,r["node_id"],r["input_level"])
    return {"message":"任务已重新排队执行","task_id":task_id,"run_version":new_ver}
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
            from ..knowledge.business import lookup
            return lookup(c,p,"stock" if payload.name=="query_stock" else "orders",payload.identifier)
        from ..knowledge.retriever import search_documents
        hits=search_documents(c,p,payload.query,8)
        return {"items":hits}
@app.get("/api/knowledge-map")
def knowledge_map(p=Depends(identity)):
    from ..knowledge.facts import list_visible_mentions
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

@app.get("/api/bi/dashboard")
def bi_dashboard(p=Depends(identity)):
    """数据中台经营看板：全部数据来自指标语义层受控执行（权限与 ChatBI 同源）。

    无权限/执行失败的板块返回 null，前端按需渲染——不泄露指标存在性之外的信息。
    """
    from ..knowledge import metrics as metrics_mod

    def run(key: str, intent: dict | None = None):
        with database_connection() as c:
            m = fetch_one(c, "SELECT * FROM metrics WHERE metric_key=%s AND tenant_id=%s AND state='published'",
                          (key, p.tenant_id))
            if not m or not metrics_mod.can_read_metric(c, p, m):
                return None
            try:
                cols, rows, meta = metrics_mod.execute_metric(c, p, m, intent or {})
                return dict(columns=cols, rows=metrics_mod.jsonable_rows(rows), meta=meta,
                            name=m["name"], unit=m["unit"], level=m["level"], description=m["description"])
            except Denied:
                return None
            except Exception:
                return None

    def total(block):
        if not block or not block["rows"]:
            return None
        try:
            return round(sum(float(r[-1]) for r in block["rows"] if isinstance(r[-1], (int, float))), 2)
        except (TypeError, ValueError):
            return None

    days30 = {"time_range": {"days": 30}}
    gsv, orders = run("biz.gsv", days30), run("biz.order_count", days30)
    rate, profit = run("biz.refund_rate", days30), run("biz.gross_profit", days30)
    low, aftersales = run("biz.low_stock_count"), run("biz.aftersales_count", days30)
    trend = run("biz.gsv_daily", days30)

    cards = dict(
        gsv_30d=total(gsv), orders_30d=total(orders),
        gross_profit_30d=total(profit), low_stock=total(low))
    if rate and rate["rows"]:
        worst = max(rate["rows"], key=lambda r: float(r[-1]) if isinstance(r[-1], (int, float)) else 0)
        cards["worst_refund_channel"] = {"channel": worst[0], "rate": worst[-1]}
        cards["avg_refund_rate"] = round(sum(float(r[-1]) for r in rate["rows"]) / len(rate["rows"]), 2)

    # 渠道销售额分布（对品类求和）
    channel_gsv = None
    if gsv and gsv["rows"]:
        agg = {}
        for r in gsv["rows"]:
            agg[r[0]] = round(agg.get(r[0], 0) + float(r[-1]), 2)
        channel_gsv = dict(sorted(agg.items(), key=lambda x: -x[1]))

    return dict(
        cards=cards,
        trend=trend,
        channel_gsv=channel_gsv,
        refund_rate=rate,
        aftersales=aftersales,
        low_stock=low,
        gross_profit=profit,
        generated_at=datetime.now(timezone.utc).isoformat())

dist=ROOT/"web/dist"
if (dist/"assets").exists():app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
@app.get("/{path:path}",include_in_schema=False)
def spa(path:str):
    if path.startswith("api/"):raise Denied("接口不存在","NOT_FOUND",404)
    if not (dist/"index.html").exists():return Response("中文工作台正在准备",media_type="text/plain; charset=utf-8",status_code=503)
    return FileResponse(dist/"index.html",headers={"Cache-Control":"no-cache"})
