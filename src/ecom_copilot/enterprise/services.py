from __future__ import annotations
import hashlib,secrets,re
from datetime import datetime,timezone
from .config import load_config
from .db import fetch_one,fetch_all,as_jsonb
from .policy import (Denied, audit, can_review, can_task, can_write, get_document, require_system, sources_valid)
from .passwords import hash_password,verify_password
from .privacy import redact

def generate_id(prefix):return prefix+"_"+secrets.token_hex(12)
def invalidate_authorization(conn,tenant,user=None):
    conn.execute("UPDATE tenants SET epoch=epoch+1 WHERE id=%s",(tenant,))
    if user:
        conn.execute("UPDATE users SET version=version+1 WHERE tenant_id=%s AND id=%s",(tenant,user))
        conn.execute("UPDATE sessions SET revoked=true WHERE tenant_id=%s AND user_id=%s",(tenant,user))
def public_user(row):
    return {k:row[k] for k in ("id","username","display_name","system_admin","active","must_change","version")}
def create_user(conn,p,username,password,display_name):
    require_system(p)
    username=username.strip().casefold()
    if not re.fullmatch(r"[\w.-]{3,64}",username):raise Denied("账号须为 3–64 位字母、数字、中文或 ._-","INVALID_ACCOUNT",400)
    if fetch_one(conn,"SELECT id FROM users WHERE tenant_id=%s AND username=%s",(p.tenant_id,username)):raise Denied("账号已存在","CONFLICT",409)
    row=fetch_one(conn,"INSERT INTO users(id,tenant_id,username,display_name,password_hash,must_change) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
        (generate_id("user"),p.tenant_id,username,display_name.strip() or username,hash_password(password,load_config().mode),load_config().mode=="production"))
    audit(conn,p,"创建待分配账号",row["id"],system=True)
    return public_user(row)
def approval(conn,p,kind,target,payload,node,required="boss"):
    a=fetch_one(conn,"INSERT INTO approvals(id,tenant_id,kind,target_id,payload,requested_by,node_id,required_role) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,state",
        (generate_id("approval"),p.tenant_id,kind,target,as_jsonb(payload),p.id,node,required))
    audit(conn,p,"提交权限或发布申请",target,node,system=kind in ("membership","user","org"),detail={"kind":kind})
    return a
def membership_request(conn,p,target,node,role,remove=False):
    require_system(p)
    u=fetch_one(conn,"SELECT * FROM users WHERE tenant_id=%s AND id=%s",(p.tenant_id,target))
    n=p.nodes.get(node)
    if not u or not n or role not in ("employee","leader","boss"):raise Denied("人员或组织无效","INVALID",400)
    if role=="boss" and n["kind"]!="company":raise Denied("老板身份只能绑定公司","INVALID",400)
    required="boss" if role!="employee" or target==p.id or n["kind"]=="company" else "leader"
    return approval(conn,p,"membership",target,dict(node_id=node,role=role,remove=remove,expected_version=u["version"]),node,required)
def user_request(conn,p,target,payload):
    require_system(p)
    u=fetch_one(conn,"SELECT * FROM users WHERE tenant_id=%s AND id=%s",(p.tenant_id,target))
    if not u:raise Denied("账号不存在","NOT_FOUND",404)
    clean={k:v for k,v in payload.items() if k in ("active","display_name","system_admin") and v is not None}
    if payload.get("new_password"):clean["password_hash"]=hash_password(payload["new_password"],load_config().mode)
    clean["expected_version"]=u["version"]
    return approval(conn,p,"user",target,clean,p.root,"boss")
def ensure_last(conn,tenant,user,remove_role=None,disable=False,remove_admin=False):
    if disable or remove_role=="boss":
        bosses=fetch_all(conn,"SELECT DISTINCT u.id FROM users u JOIN memberships m ON m.user_id=u.id JOIN org_nodes n ON n.id=m.node_id WHERE u.tenant_id=%s AND u.active AND m.active AND m.role='boss' AND n.kind='company'",(tenant,))
        if len(bosses)<=1 and any(x["id"]==user for x in bosses):raise Denied("不能停用或移除最后一位老板","LAST_OWNER",409)
    if disable or remove_admin:
        admins=fetch_all(conn,"SELECT id FROM users WHERE tenant_id=%s AND active AND system_admin",(tenant,))
        if len(admins)<=1 and any(x["id"]==user for x in admins):raise Denied("不能停用或移除最后一位管理员","LAST_ADMIN",409)
def decide(conn,p,aid,approve,comment):
    a=fetch_one(conn,"SELECT * FROM approvals WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,aid))
    if not a:raise Denied("申请不存在","NOT_FOUND",404)
    if a["state"]!="pending":raise Denied("申请已经处理","CONFLICT",409)
    if not p.boss and (a["required_role"]=="boss" or not p.manages(a["node_id"]) or p.id==a["requested_by"]):
        raise Denied("此申请需要对应业务负责人审批")
    v=a["payload"];kind=a["kind"]
    if approve:
        if kind in ("membership","user"):
            u=fetch_one(conn,"SELECT * FROM users WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,a["target_id"]))
            if not u or u["version"]!=v["expected_version"]:raise Denied("人员状态已变化，请重新申请","STALE",409)
            if kind=="membership":
                if v["role"]!="employee" and not p.boss:raise Denied()
                if v["remove"]:
                    ensure_last(conn,p.tenant_id,u["id"],v["role"])
                    conn.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id=%s AND node_id=%s AND role=%s",(p.tenant_id,u["id"],v["node_id"],v["role"]))
                else:
                    conn.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,node_id,role) DO UPDATE SET active=true",(p.tenant_id,u["id"],v["node_id"],v["role"]))
            else:
                ensure_last(conn,p.tenant_id,u["id"],disable=v.get("active") is False,remove_admin=v.get("system_admin") is False)
                for field in ("active","display_name","system_admin","password_hash"):
                    if field in v:
                        conn.execute("UPDATE users SET "+field+"=%s WHERE tenant_id=%s AND id=%s",(v[field],p.tenant_id,u["id"]))
                if "password_hash" in v:conn.execute("UPDATE users SET must_change=%s WHERE id=%s",(load_config().mode=="production",u["id"]))
            invalidate_authorization(conn,p.tenant_id,u["id"])
        elif kind in ("document_policy","grant","publish"):
            d=get_document(conn,p,a["target_id"])
            conn.execute("SELECT id FROM documents WHERE id=%s FOR UPDATE",(d["id"],))
            d=get_document(conn,p,d["id"])
            if d["acl_version"]!=v["expected_acl"] or d["current_version"]!=v["expected_version"]:raise Denied("资料版本或权限已变化，请重新申请","STALE",409)
            if kind=="publish":
                if not can_write(conn,p,d) or (not p.boss and (d["level"]>2 or a["requested_by"]==p.id)):raise Denied("需要另一位负责人审核发布")
                if d["state"]!="review" or not fetch_one(conn,"SELECT id FROM chunks WHERE document_id=%s AND version=%s",(d["id"],d["current_version"])):raise Denied("资料尚未完成解析","NOT_READY",409)
                conn.execute("UPDATE documents SET state='published',acl_version=acl_version+1,updated_at=now() WHERE id=%s",(d["id"],))
            elif kind=="grant":
                if not p.boss:raise Denied()
                target=fetch_one(conn,"SELECT id FROM users WHERE tenant_id=%s AND id=%s AND active",(p.tenant_id,v["user_id"]))
                if not target:raise Denied("授权人员已失效","STALE",409)
                if v.get("revoke"):
                    conn.execute("DELETE FROM document_grants WHERE document_id=%s AND user_id=%s",(d["id"],target["id"]))
                else:
                    conn.execute("INSERT INTO document_grants(tenant_id,document_id,user_id,effect,can_read,can_download,can_write,expires_at,granted_by) VALUES(%s,%s,%s,%s,true,%s,%s,%s,%s) ON CONFLICT(document_id,user_id) DO UPDATE SET effect=EXCLUDED.effect,can_read=true,can_download=EXCLUDED.can_download,can_write=EXCLUDED.can_write,expires_at=EXCLUDED.expires_at,granted_by=EXCLUDED.granted_by",
                      (p.tenant_id,d["id"],target["id"],v.get("effect","allow"),v.get("can_download",False),False,v.get("expires_at"),p.id))
                conn.execute("UPDATE documents SET acl_version=acl_version+1 WHERE id=%s",(d["id"],))
            else:
                if not p.boss:raise Denied()
                for field in ("scope","node_id","level","ai_allowed","download_allowed"):
                    if field in v:conn.execute("UPDATE documents SET "+field+"=%s WHERE id=%s",(v[field],d["id"]))
                conn.execute("UPDATE documents SET acl_version=acl_version+1,updated_at=now() WHERE id=%s",(d["id"],))
            invalidate_authorization(conn,p.tenant_id)
        elif kind=="org":
            if not p.boss:raise Denied()
            n=fetch_one(conn,"SELECT * FROM org_nodes WHERE tenant_id=%s AND id=%s FOR UPDATE",(p.tenant_id,a["target_id"]))
            if not n or n["version"]!=v["expected_version"]:raise Denied("组织状态已变化","STALE",409)
            parent=v.get("parent_id",n["parent_id"])
            if parent and (parent not in p.nodes or p.covers(n["id"],parent)):raise Denied("组织不能形成循环","INVALID",400)
            if n["kind"]=="company":raise Denied("公司根节点不能移动或停用","INVALID",400)
            parent_node=p.nodes.get(parent)
            if not parent_node or (n["kind"]=="department" and parent_node["kind"]!="company") or (n["kind"]=="team" and parent_node["kind"]!="department"):
                raise Denied("部门必须属于公司，小组必须属于部门","INVALID",400)
            if v.get("active") is False and fetch_one(conn,"SELECT id FROM org_nodes WHERE parent_id=%s AND active",(n["id"],)):raise Denied("请先处理下级组织","CONFLICT",409)
            conn.execute("UPDATE org_nodes SET name=%s,parent_id=%s,active=%s,version=version+1 WHERE id=%s",(v.get("name",n["name"]),parent,v.get("active",n["active"]),n["id"]))
            for u in fetch_all(conn,"SELECT DISTINCT user_id FROM memberships WHERE tenant_id=%s",(p.tenant_id,)):invalidate_authorization(conn,p.tenant_id,u["user_id"])
        else:raise Denied("申请类型不支持","INVALID",400)
    conn.execute("UPDATE approvals SET state=%s,decided_by=%s,comment=%s,decided_at=now() WHERE id=%s",("approved" if approve else "rejected",p.id,comment[:1000],aid))
    audit(conn,p,"批准申请" if approve else "拒绝申请",a["target_id"],a["node_id"],system=kind in ("membership","user","org"),detail={"kind":kind,"decision":"approved" if approve else "rejected"})
    return {"message":"申请已批准" if approve else "申请已拒绝"}

def create_job(conn,p,kind,node,payload,question="",level=1,key=None):
    if node not in p.nodes or (not p.boss and not any(p.overlaps(m["node_id"],node) and not (m["role"]=="employee" and p.nodes.get(m["node_id"],{}).get("kind")=="company" and node!=m["node_id"]) for m in p.memberships) and node!=p.root):
        raise Denied("不属于该组织范围")
    import json
    fingerprint=hashlib.sha256(json.dumps(dict(kind=kind,node=node,payload=payload,question=question,level=level),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if key:
        existing=fetch_one(conn,"SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND idempotency_key=%s",(p.tenant_id,p.id,key))
        if existing:
            if existing["fingerprint"]!=fingerprint:raise Denied("重复提交标识对应不同内容","IDEMPOTENCY_CONFLICT",409)
            return existing
    conn.execute("SELECT id FROM tenants WHERE id=%s FOR UPDATE",(p.tenant_id,))
    if key:
        existing=fetch_one(conn,"SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND idempotency_key=%s",(p.tenant_id,p.id,key))
        if existing:
            if existing["fingerprint"]!=fingerprint:raise Denied("重复提交标识对应不同内容","IDEMPOTENCY_CONFLICT",409)
            return existing
    count=fetch_one(conn,"SELECT count(*) AS n FROM jobs WHERE tenant_id=%s AND state IN ('queued','running')",(p.tenant_id,))["n"]
    if count>=load_config().max_pending:raise Denied("任务较多，请稍后重试","QUEUE_FULL",429)
    job=fetch_one(conn,"INSERT INTO jobs(id,tenant_id,owner_id,owner_version,node_id,kind,question,input_level,payload,idempotency_key,fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
      (generate_id("task"),p.tenant_id,p.id,p.version,node,kind,question,level,as_jsonb(payload),key,fingerprint))
    audit(conn,p,"提交问答" if kind=="ask" else "提交资料解析",job["id"],node,level,detail={"kind":kind})
    return job

def new_document(conn,p,title,body,node_id,scope,level,blob=None,mime="text/plain"):
    node=node_id
    if node not in p.nodes:raise Denied("组织不存在","INVALID",400)
    if scope=="company" and node!=p.root:raise Denied("公司共享资料必须归属公司","INVALID",400)
    allowed=p.boss or (p.manages(node) and scope!="company" and level<=2) or (p.system_admin and scope=="company" and level==1)
    if not allowed:raise Denied("不能在该范围创建资料")
    did=generate_id("doc")
    conn.execute("INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,download_allowed,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'ingesting')",
        (did,p.tenant_id,node,p.id,title,scope,level,level<3,level==1))
    conn.execute("INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,blob_name,mime_type,created_by,policy) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s)",
        (p.tenant_id,did,title,body,hashlib.sha256(body.encode()).hexdigest(),blob,mime,p.id,as_jsonb(dict(node_id=node,scope=scope,level=level))))
    job=create_job(conn,p,"ingest",node,dict(document_id=did,version=1),level=level)
    audit(conn,p,"创建资料草稿",did,node,level)
    return dict(id=did,task_id=job["id"],message="资料正在解析，审核发布后才可见")
def edit_document(conn,p,d,title,body,version):
    if not can_write(conn,p,d):raise Denied("没有资料写权限")
    locked=fetch_one(conn,"SELECT * FROM documents WHERE id=%s FOR UPDATE",(d["id"],))
    original=fetch_one(conn,"SELECT body FROM document_versions WHERE document_id=%s AND version=%s",(d["id"],locked["current_version"]))["body"]
    if not p.boss and original!=redact(original):raise Denied("资料包含敏感字段，原文编辑需要老板身份","FIELD_WRITE_RESTRICTED",403)
    if locked["current_version"]!=version:raise Denied("资料已被修改，请刷新后重试","VERSION_CONFLICT",409)
    version+=1
    conn.execute("INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,created_by,policy) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (p.tenant_id,d["id"],version,title,body,hashlib.sha256(body.encode()).hexdigest(),p.id,as_jsonb({k:d[k] for k in ("node_id","scope","level")})))
    conn.execute("UPDATE documents SET title=%s,current_version=%s,state='ingesting',acl_version=acl_version+1,updated_at=now() WHERE id=%s",(title,version,d["id"]))
    invalidate_authorization(conn,p.tenant_id)
    job=create_job(conn,p,"ingest",d["node_id"],dict(document_id=d["id"],version=version),level=d["level"])
    audit(conn,p,"编辑资料版本",d["id"],d["node_id"],d["level"],detail={"version":version})
    return {"message":"新版本正在解析，等待发布审核","task_id":job["id"]}

def present_job(conn,p,job,full=True):
    if not can_task(p,job):raise Denied("任务不存在或没有访问权限","NOT_FOUND",404)
    valid=job["state"]!="legacy" and sources_valid(conn,p,job["sources"])
    result=job["result"] if valid and (job["state"]=="completed" or (job["state"]=="review" and can_review(p,job))) else None
    return dict(id=job["id"],question=(job["question"] if p.boss or p.id==job["owner_id"] else redact(job["question"])) if valid else "来源权限已变化，请重新查询",
      kind=job["kind"],state=job["state"],stage=job["stage"],progress=job["progress"],
      created_at=job["created_at"],finished_at=job["finished_at"],error=job["error"],
      input_level=job["input_level"],node_id=job["node_id"],owner_id=job["owner_id"],
      result=result if full else None,restricted=not valid,can_review=can_review(p,job) and valid,
      can_cancel=p.id==job["owner_id"] or p.boss)
