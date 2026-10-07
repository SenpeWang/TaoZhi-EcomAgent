"""所有页面、接口、索引与工作流共用的权限决策。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from .db import fetch_one,fetch_all

class Denied(Exception):
    def __init__(self,message="没有访问权限",code="FORBIDDEN",status=403):
        self.message,self.code,self.status=message,code,status
        super().__init__(message)

@dataclass
class Principal:
    id: str
    tenant_id: str
    username: str
    display_name: str
    system_admin: bool
    version: int
    must_change: bool
    memberships: list
    nodes: dict

    def covers(self,a,b):
        seen=set()
        while b and b not in seen:
            if a==b:return True
            seen.add(b);b=self.nodes.get(b,{}).get("parent_id")
        return False
    def overlaps(self,a,b):return self.covers(a,b) or self.covers(b,a)
    @property
    def boss(self):return any(m["role"]=="boss" and self.nodes.get(m["node_id"],{}).get("kind")=="company" for m in self.memberships)
    @property
    def leader(self):return any(m["role"]=="leader" for m in self.memberships)
    @property
    def root(self):return next((n["id"] for n in self.nodes.values() if n["kind"]=="company"),"")
    def manages(self,node):
        return self.boss or any(m["role"]=="leader" and self.covers(m["node_id"],node) for m in self.memberships)
    def grade(self,node,scope="org"):
        if self.boss:return 3
        if scope=="company":return 2 if self.leader else 1
        levels=[2 if m["role"]=="leader" else 1 for m in self.memberships if self.overlaps(m["node_id"],node) and not (m["role"]=="employee" and self.nodes.get(m["node_id"],{}).get("kind")=="company" and node!=m["node_id"])]
        return max(levels,default=0)
    def public(self):
        labels=[]
        if self.system_admin:labels.append("管理员")
        if self.boss:labels.append("老板")
        elif self.leader:labels.append("组长")
        elif self.memberships or not self.system_admin:labels.append("员工")
        return dict(id=self.id,username=self.username,display_name=self.display_name,
            system_admin=self.system_admin,is_boss=self.boss,is_leader=self.leader,roles=labels,
            must_change=self.must_change,version=self.version,memberships=[dict(**m,name=self.nodes.get(m["node_id"],{}).get("name","")) for m in self.memberships],
            capabilities=dict(system=self.system_admin or self.boss,write=self.system_admin or self.leader or self.boss,
                review=self.leader or self.boss,permissions=self.system_admin or self.leader or self.boss))

def load_principal(conn,user_id):
    u=fetch_one(conn,"SELECT u.* FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE u.id=%s AND u.active AND t.enabled",(user_id,))
    if not u:raise Denied("账号已停用或不存在","SESSION_INVALID",401)
    nodes={n["id"]:n for n in fetch_all(conn,"SELECT * FROM org_nodes WHERE tenant_id=%s AND active",(u["tenant_id"],))}
    memberships=fetch_all(conn,"SELECT m.node_id,m.role FROM memberships m JOIN org_nodes n ON n.id=m.node_id WHERE m.tenant_id=%s AND m.user_id=%s AND m.active AND n.active",(u["tenant_id"],user_id))
    return Principal(u["id"],u["tenant_id"],u["username"],u["display_name"],u["system_admin"],u["version"],u["must_change"],memberships,nodes)

def require_system(p):
    if not (p.system_admin or p.boss):raise Denied("此操作需要管理员或老板身份")
def get_document(conn,p,doc_id):
    d=fetch_one(conn,"SELECT * FROM documents WHERE tenant_id=%s AND id=%s AND state<>'deleted'",(p.tenant_id,doc_id))
    if not d:raise Denied("资料不存在或没有访问权限","NOT_FOUND",404)
    return d
def get_document_grant(conn,p,d):
    return fetch_one(conn,"SELECT * FROM document_grants WHERE tenant_id=%s AND document_id=%s AND user_id=%s AND (expires_at IS NULL OR expires_at>now())",(p.tenant_id,d["id"],p.id))
def can_read(conn,p,d,include_draft=False):
    if d["tenant_id"]!=p.tenant_id or d["state"]=="deleted":return False
    g=get_document_grant(conn,p,d)
    if g and g["effect"]=="deny":return False
    if d["state"]!="published":
        return include_draft and can_write(conn,p,d)
    if g and g["effect"]=="allow" and g["can_read"]:return True
    if p.boss:return True
    return d["scope"]!="selected" and p.grade(d["node_id"],d["scope"])>=d["level"]
def can_write(conn,p,d):
    if d["tenant_id"]!=p.tenant_id or d["state"]=="deleted":return False
    g=get_document_grant(conn,p,d)
    if g and g["effect"]=="deny":return False
    return p.boss or (p.manages(d["node_id"]) and d["level"]<=2 and d["scope"]!="company") or (p.system_admin and d["scope"]=="company" and d["level"]==1)
def can_download(conn,p,d):
    if not can_read(conn,p,d):return False
    g=get_document_grant(conn,p,d)
    return p.boss or bool(g and g["effect"]=="allow" and g["can_download"]) or (d["scope"]!="selected" and p.grade(d["node_id"],d["scope"])>=d["level"] and d["download_allowed"])
def require_document_read(conn,p,d):
    if not can_read(conn,p,d,True):raise Denied("资料不存在或没有访问权限","NOT_FOUND",404)
def source_valid(conn,p,source,external=False):
    if source.get("kind")=="business":
        if source.get("tenant_id")!=p.tenant_id or p.grade(source.get("node_id"),"org")<source.get("level",3) or datetime.now(timezone.utc).timestamp()-source.get("as_of",0)>60:return False
        if external and source.get("level",3)>=3:return False
        from .business import lookup
        fresh=lookup(conn,p,source.get("resource"),source.get("identifier"))
        return bool(fresh.get("available"))
    d=fetch_one(conn,"SELECT * FROM documents WHERE tenant_id=%s AND id=%s",(p.tenant_id,source.get("document_id")))
    return bool(d and d["state"]=="published" and can_read(conn,p,d) and d["current_version"]==source.get("version") and d["acl_version"]==source.get("acl_version") and (not external or d["ai_allowed"]))
def sources_valid(conn,p,sources,external=False):return all(source_valid(conn,p,s,external) for s in sources)
def can_task(p,job):
    if job["tenant_id"]!=p.tenant_id:return False
    return p.id==job["owner_id"] or (p.manages(job["node_id"]) and p.grade(job["node_id"])>=job["input_level"])
def can_review(p,job):return job["tenant_id"]==p.tenant_id and p.manages(job["node_id"]) and p.grade(job["node_id"])>=job["input_level"]
def audit(conn,p,action,target="",node=None,level=1,system=False,detail=None):
    from .db import as_jsonb
    allowed={k:v for k,v in (detail or {}).items() if k in ("role","state","version","decision","reset_password","scope","level","count","kind")}
    conn.execute("INSERT INTO audit_events(tenant_id,actor_id,action,target_id,node_id,level,system,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (p.tenant_id,p.id,action,target,node or p.root,level,system,as_jsonb(allowed)))
