# 持久化与数据仓储实现：承接带 conn 数据库连接的数据查询与权限前置校验
from __future__ import annotations
from datetime import datetime, timezone
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.access import can_task, can_review, evaluate_read_permission, evaluate_write_permission, evaluate_download_permission
from taozhi.knowledge.db import fetch_one, fetch_all, as_jsonb

def load_principal(conn, user_id: str) -> Principal:
    u = fetch_one(conn, "SELECT u.* FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE u.id=%s AND u.active AND t.enabled", (user_id,))
    if not u:
        raise Denied("账号已停用或不存在", "SESSION_INVALID", 401)
    nodes = {n["id"]: n for n in fetch_all(conn, "SELECT * FROM org_nodes WHERE tenant_id=%s AND active", (u["tenant_id"],))}
    memberships = fetch_all(conn, "SELECT m.node_id, m.role FROM memberships m JOIN org_nodes n ON n.id=m.node_id WHERE m.tenant_id=%s AND m.user_id=%s AND m.active AND n.active", (u["tenant_id"], user_id))
    return Principal(u["id"], u["tenant_id"], u["username"], u["display_name"], u["system_admin"], u["version"], u["must_change"], memberships, nodes)

def require_system(p: Principal) -> None:
    if not (p.system_admin or p.boss):
        raise Denied("此操作需要管理员或老板身份")

def get_document(conn, p: Principal, doc_id: str) -> dict:
    d = fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s AND state<>'deleted'", (p.tenant_id, doc_id))
    if not d:
        raise Denied("资料不存在或没有访问权限", "NOT_FOUND", 404)
    return d

def get_document_grant(conn, p: Principal, d: dict) -> dict | None:
    doc_id = d.get("id") if isinstance(d, dict) else d
    return fetch_one(conn, "SELECT * FROM document_grants WHERE tenant_id=%s AND document_id=%s AND user_id=%s AND (expires_at IS NULL OR expires_at>now())", (p.tenant_id, doc_id, p.id))

def can_write(conn, p: Principal, d: dict) -> bool:
    g = get_document_grant(conn, p, d)
    return evaluate_write_permission(p, d, g)

def can_read(conn, p: Principal, d: dict, include_draft: bool = False) -> bool:
    g = get_document_grant(conn, p, d)
    # 未发布资料是否可读取决于写权限；仅在确有必要时才多查一次
    needs_write = include_draft and d.get("state") != "published"
    can_w = can_write(conn, p, d) if needs_write else False
    return evaluate_read_permission(p, d, g, include_draft=include_draft, can_write_flag=can_w)

def can_download(conn, p: Principal, d: dict) -> bool:
    g = get_document_grant(conn, p, d)
    can_r = can_read(conn, p, d)
    return evaluate_download_permission(p, d, g, can_read_flag=can_r)

def require_document_read(conn, p: Principal, d: dict) -> None:
    if not can_read(conn, p, d, True):
        raise Denied("资料不存在或没有访问权限", "NOT_FOUND", 404)

def source_valid(conn, p: Principal, source: dict, external: bool = False) -> bool:
    if source.get("kind") == "business":
        if source.get("tenant_id") != p.tenant_id or p.grade(source.get("node_id"), "org") < source.get("level", 3) or datetime.now(timezone.utc).timestamp() - source.get("as_of", 0) > 60:
            return False
        if external and source.get("level", 3) >= 3:
            return False
        from taozhi.knowledge.business import lookup
        fresh = lookup(conn, p, source.get("resource"), source.get("identifier"))
        return bool(fresh.get("available"))
    d = fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s", (p.tenant_id, source.get("document_id")))
    return bool(d and d["state"] == "published" and can_read(conn, p, d) and d["current_version"] == source.get("version") and d["acl_version"] == source.get("acl_version") and (not external or d["ai_allowed"]))

def sources_valid(conn, p: Principal, sources: list, external: bool = False) -> bool:
    return all(source_valid(conn, p, s, external) for s in sources)

def audit(conn, p: Principal, action: str, target: str = "", node: str | None = None, level: int = 1, system: bool = False, detail: dict | None = None) -> None:
    allowed = {k: v for k, v in (detail or {}).items() if k in ("role", "state", "version", "decision", "reset_password", "scope", "level", "count", "kind")}
    conn.execute(
        "INSERT INTO audit_events(tenant_id,actor_id,action,target_id,node_id,level,system,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (p.tenant_id, p.id, action, target, node or p.root, level, system, as_jsonb(allowed))
    )
