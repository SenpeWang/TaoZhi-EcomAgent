"""持久化访问控制与来源实时核验仓储：承接基于数据库连接的权限、来源有效性与审计落库。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from taozhi.domain.access import evaluate_download_permission, evaluate_read_permission, evaluate_write_permission
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.knowledge.db import as_jsonb, fetch_all, fetch_one


def load_principal(conn: Any, user_id: str) -> Principal:
    """从数据库加载在职用户主体及其生效组织树和角色任职。

    Args:
        conn: 数据库连接。
        user_id: 用户主键编号。

    Returns:
        构建完毕的 Principal 实体。

    Raises:
        Denied: 当账号不存在、已停用或租户被禁用时抛出 401。
    """
    u = fetch_one(conn, "SELECT u.* FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE u.id=%s AND u.active AND t.enabled", (user_id,))
    if not u:
        raise Denied("账号已停用或不存在", "SESSION_INVALID", 401)
    nodes = {n["id"]: n for n in fetch_all(conn, "SELECT * FROM org_nodes WHERE tenant_id=%s AND active", (u["tenant_id"],))}
    memberships = fetch_all(
        conn,
        "SELECT m.node_id, m.role FROM memberships m JOIN org_nodes n ON n.id=m.node_id "
        "WHERE m.tenant_id=%s AND m.user_id=%s AND m.active AND n.active",
        (u["tenant_id"], user_id),
    )
    return Principal(u["id"], u["tenant_id"], u["username"], u["display_name"], u["system_admin"], u["version"], u["must_change"], memberships, nodes)


def require_system(p: Principal) -> None:
    """校验当前主体是否为系统管理员或公司老板。"""
    if not (p.system_admin or p.boss):
        raise Denied("此操作需要管理员或老板身份")


def get_document(conn: Any, p: Principal, doc_id: str) -> dict[str, Any]:
    """按租户隔离读取未删除的文档记录。

    Args:
        conn: 数据库连接。
        p: 当前请求主体。
        doc_id: 文档主键编号。

    Returns:
        文档行字典。
    """
    d = fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s AND state<>'deleted'", (p.tenant_id, doc_id))
    if not d:
        raise Denied("资料不存在或没有访问权限", "NOT_FOUND", 404)
    return d


def get_document_grant(conn: Any, p: Principal, d: dict | str) -> dict[str, Any] | None:
    """查询指定主体针对文档的当前有效个人特例授权。"""
    doc_id = d.get("id") if isinstance(d, dict) else d
    return fetch_one(
        conn,
        "SELECT * FROM document_grants WHERE tenant_id=%s AND document_id=%s AND user_id=%s AND (expires_at IS NULL OR expires_at>now())",
        (p.tenant_id, doc_id, p.id),
    )


def can_write(conn: Any, p: Principal, d: dict[str, Any]) -> bool:
    """判定主体是否有权修改或删除目标文档。"""
    g = get_document_grant(conn, p, d)
    return evaluate_write_permission(p, d, g)


def can_read(conn: Any, p: Principal, d: dict[str, Any], include_draft: bool = False) -> bool:
    """判定主体是否有权阅读目标文档。

    Args:
        conn: 数据库连接。
        p: 当前请求主体。
        d: 文档记录字典。
        include_draft: 是否允许拥有写权限者查看非发布态草稿。

    Returns:
        允许阅读返回 True。
    """
    g = get_document_grant(conn, p, d)
    needs_write = include_draft and d.get("state") != "published"
    can_w = can_write(conn, p, d) if needs_write else False
    return evaluate_read_permission(p, d, g, include_draft=include_draft, can_write_flag=can_w)


def can_download(conn: Any, p: Principal, d: dict[str, Any]) -> bool:
    """判定主体是否有权下载目标文档。"""
    g = get_document_grant(conn, p, d)
    can_r = can_read(conn, p, d)
    return evaluate_download_permission(p, d, g, can_read_flag=can_r)


def require_document_read(conn: Any, p: Principal, d: dict[str, Any]) -> None:
    """强制断言阅读权限（允许写作者查看草稿），否则抛出 404 防枚举。"""
    if not can_read(conn, p, d, True):
        raise Denied("资料不存在或没有访问权限", "NOT_FOUND", 404)


def source_valid(conn: Any, p: Principal, source: dict[str, Any], external: bool = False) -> bool:
    """实时核验单条引用来源（文档版本/ACL 或业务快照）是否仍对主体有效。

    Args:
        conn: 数据库连接。
        p: 当前查看或审核主体。
        source: 任务记录中的引用元数据字典。
        external: 是否涉及外发大模型处理许可校验。

    Returns:
        来源仍然有效且主体有权限时返回 True。
    """
    if source.get("kind") == "business":
        if (
            source.get("tenant_id") != p.tenant_id
            or p.grade(source.get("node_id"), "org") < source.get("level", 3)
            or datetime.now(timezone.utc).timestamp() - source.get("as_of", 0) > 60
        ):
            return False
        if external and source.get("level", 3) >= 3:
            return False
        from taozhi.knowledge.business import lookup

        fresh = lookup(conn, p, source.get("resource"), source.get("identifier"))
        return bool(fresh.get("available"))
    d = fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s", (p.tenant_id, source.get("document_id")))
    return bool(
        d
        and d["state"] == "published"
        and can_read(conn, p, d)
        and d["current_version"] == source.get("version")
        and d["acl_version"] == source.get("acl_version")
        and (not external or d["ai_allowed"])
    )


def sources_valid(conn: Any, p: Principal, sources: list[dict[str, Any]], external: bool = False) -> bool:
    """批量实时核验任务依赖的全部引用来源是否仍有效。"""
    return all(source_valid(conn, p, s, external) for s in sources)


def audit(
    conn: Any,
    p: Principal,
    action: str,
    target: str = "",
    node: str | None = None,
    level: int = 1,
    system: bool = False,
    detail: dict[str, Any] | None = None,
) -> None:
    """将业务安全操作写入租户不可篡改审计事件表。

    Args:
        conn: 数据库连接。
        p: 执行操作的主体。
        action: 操作中文名称。
        target: 目标对象编号。
        node: 关联组织节点编号。
        level: 事件关联数据密级。
        system: 是否为系统/组织/账号管理类全局审计。
        detail: 白名单过滤后的结构化附加元数据。
    """
    allowed = {
        k: v
        for k, v in (detail or {}).items()
        if k in ("role", "state", "version", "decision", "reset_password", "scope", "level", "count", "kind")
    }
    conn.execute(
        "INSERT INTO audit_events(tenant_id,actor_id,action,target_id,node_id,level,system,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (p.tenant_id, p.id, action, target, node or p.root, level, system, as_jsonb(allowed)),
    )
