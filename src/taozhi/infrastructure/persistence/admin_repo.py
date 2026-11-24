"""租户、组织节点、账号、审批与审计日志仓储。"""
from __future__ import annotations

from typing import Any

from taozhi.infrastructure.persistence.access_repo import audit
from taozhi.infrastructure.persistence.job_repo import generate_id
from taozhi.infrastructure.persistence.database import as_jsonb, fetch_all, fetch_one


def invalidate_authorization(conn: Any, tenant_id: str, user_id: str | None = None) -> None:
    """递增租户/用户权限代际版本并吊销其现有会话。"""
    conn.execute("UPDATE tenants SET epoch=epoch+1 WHERE id=%s", (tenant_id,))
    if user_id:
        conn.execute("UPDATE users SET version=version+1 WHERE tenant_id=%s AND id=%s", (tenant_id, user_id))
        conn.execute("UPDATE sessions SET revoked=true WHERE tenant_id=%s AND user_id=%s", (tenant_id, user_id))


def create_approval(conn: Any, p: Any, kind: str, target_id: str, payload: dict, node_id: str, required_role: str = "boss") -> dict:
    """创建审批申请单并写入审计日志。"""
    row = fetch_one(
        conn,
        "INSERT INTO approvals(id,tenant_id,kind,target_id,payload,requested_by,node_id,required_role) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,state",
        (generate_id("approval"), p.tenant_id, kind, target_id, as_jsonb(payload), p.id, node_id, required_role),
    )
    audit(conn, p, "提交权限或发布申请", target_id, node_id, system=kind in ("membership", "user", "org"), detail={"kind": kind})
    return row


def find_tenant_by_code(conn: Any, tenant_code: str) -> dict | None:
    """按租户编码查询租户信息。"""
    return fetch_one(conn, "SELECT id, name FROM tenants WHERE code=%s", (tenant_code,))


def list_quick_login_rows(conn: Any, tenant_id: str) -> list[dict]:
    """查询用于快捷体验账号展示的现有正式在职账号清单。"""
    return fetch_all(
        conn,
        "SELECT u.username,u.display_name,u.system_admin,"
        "coalesce(string_agg(m.role,','),'') AS roles "
        "FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.tenant_id=u.tenant_id "
        "WHERE u.tenant_id=%s AND u.active AND u.username NOT LIKE %s ESCAPE '\\' "
        "GROUP BY u.id,u.username,u.display_name,u.system_admin ORDER BY u.username",
        (tenant_id, "syn\\_%"),
    )


def revoke_session(conn: Any, session_hash: str) -> None:
    """作废指定哈希对应的登录会话。"""
    conn.execute("UPDATE sessions SET revoked=true WHERE id=%s", (session_hash,))


def get_user(conn: Any, tenant_id: str, user_id: str, *, for_update: bool = False, active_only: bool = False) -> dict | None:
    """读取租户下指定账号记录。"""
    extra = " AND active" if active_only else ""
    suffix = " FOR UPDATE" if for_update else ""
    return fetch_one(conn, f"SELECT * FROM users WHERE tenant_id=%s AND id=%s{extra}{suffix}", (tenant_id, user_id))


def get_user_by_id_for_update(conn: Any, user_id: str) -> dict | None:
    """按主键加锁读取用户账号记录。"""
    return fetch_one(conn, "SELECT * FROM users WHERE id=%s FOR UPDATE", (user_id,))


def user_exists_by_username(conn: Any, tenant_id: str, username: str) -> bool:
    """检查用户名在租户内是否已被占用。"""
    return bool(fetch_one(conn, "SELECT id FROM users WHERE tenant_id=%s AND username=%s", (tenant_id, username)))


def insert_user(conn: Any, *, tenant_id: str, username: str, display_name: str, password_hash: str, must_change: bool) -> dict:
    """插入新用户并返回完整行。"""
    return fetch_one(
        conn,
        "INSERT INTO users(id,tenant_id,username,display_name,password_hash,must_change) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
        (generate_id("user"), tenant_id, username, display_name, password_hash, must_change),
    )


def update_user_password(conn: Any, user_id: str, password_hash: str, *, must_change: bool = False) -> None:
    """更新用户口令哈希并重置强制改密标记。"""
    conn.execute("UPDATE users SET password_hash=%s,must_change=%s WHERE id=%s", (password_hash, must_change, user_id))


def update_user_fields(conn: Any, tenant_id: str, user_id: str, fields: dict[str, Any], *, is_production: bool) -> None:
    """批量更新用户审批变更字段。"""
    for field in ("active", "display_name", "system_admin", "password_hash"):
        if field in fields:
            conn.execute("UPDATE users SET " + field + "=%s WHERE tenant_id=%s AND id=%s", (fields[field], tenant_id, user_id))
    if "password_hash" in fields:
        conn.execute("UPDATE users SET must_change=%s WHERE id=%s", (is_production, user_id))


def list_users_in_tenant(conn: Any, tenant_id: str) -> list[dict]:
    """列出租户内所有账号记录。"""
    return fetch_all(conn, "SELECT * FROM users WHERE tenant_id=%s ORDER BY created_at", (tenant_id,))


def list_user_memberships(conn: Any, tenant_id: str, user_id: str) -> list[dict]:
    """读取用户当前生效的组织隶属关系（含部门名称）。"""
    return fetch_all(
        conn,
        "SELECT m.node_id, m.role, n.name FROM memberships m "
        "LEFT JOIN org_nodes n ON n.tenant_id=m.tenant_id AND n.id=m.node_id "
        "WHERE m.tenant_id=%s AND m.user_id=%s AND m.active",
        (tenant_id, user_id),
    )


def list_distinct_member_user_ids(conn: Any, tenant_id: str) -> list[str]:
    """列出租户内拥有组织关系的用户标识列表。"""
    rows = fetch_all(conn, "SELECT DISTINCT user_id FROM memberships WHERE tenant_id=%s", (tenant_id,))
    return [str(r["user_id"]) for r in rows]


def upsert_or_delete_membership(conn: Any, tenant_id: str, user_id: str, node_id: str, role: str, *, remove: bool) -> None:
    """授予或解除组织成员身份。"""
    if remove:
        conn.execute(
            "DELETE FROM memberships WHERE tenant_id=%s AND user_id=%s AND node_id=%s AND role=%s",
            (tenant_id, user_id, node_id, role),
        )
    else:
        conn.execute(
            "INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(user_id,node_id,role) DO UPDATE SET active=true",
            (tenant_id, user_id, node_id, role),
        )


def list_active_company_bosses(conn: Any, tenant_id: str) -> list[dict]:
    """查询公司节点下活跃老板账号，用于防止删除最后一个所有者。"""
    return fetch_all(
        conn,
        "SELECT DISTINCT u.id FROM users u JOIN memberships m ON m.user_id=u.id JOIN org_nodes n ON n.id=m.node_id "
        "WHERE u.tenant_id=%s AND u.active AND m.active AND m.role='boss' AND n.kind='company'",
        (tenant_id,),
    )


def list_active_system_admins(conn: Any, tenant_id: str) -> list[dict]:
    """查询在职系统管理员账号，用于防止停用最后一位管理员。"""
    return fetch_all(conn, "SELECT id FROM users WHERE tenant_id=%s AND active AND system_admin", (tenant_id,))


def insert_org_node(conn: Any, tenant_id: str, parent_id: str, name: str, kind: str) -> dict:
    """创建新组织树节点。"""
    return fetch_one(
        conn,
        "INSERT INTO org_nodes(id,tenant_id,parent_id,name,kind) VALUES(%s,%s,%s,%s,%s) RETURNING *",
        (generate_id("org"), tenant_id, parent_id, name, kind),
    )


def get_org_node_for_update(conn: Any, tenant_id: str, node_id: str) -> dict | None:
    """加锁读取组织节点记录。"""
    return fetch_one(conn, "SELECT * FROM org_nodes WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, node_id))


def has_active_child_org(conn: Any, node_id: str) -> bool:
    """判断组织节点下是否存在活跃下级组织。"""
    return bool(fetch_one(conn, "SELECT id FROM org_nodes WHERE parent_id=%s AND active", (node_id,)))


def update_org_node(conn: Any, node_id: str, name: str, parent_id: str | None, active: bool) -> None:
    """更新组织节点名称、父节点与启用状态。"""
    conn.execute(
        "UPDATE org_nodes SET name=%s,parent_id=%s,active=%s,version=version+1 WHERE id=%s",
        (name, parent_id, active, node_id),
    )


def list_approvals(conn: Any, tenant_id: str) -> list[dict]:
    """按创建时间倒序列出待审及历史申请单。"""
    return fetch_all(conn, "SELECT * FROM approvals WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 500", (tenant_id,))


def get_approval_for_update(conn: Any, tenant_id: str, aid: str) -> dict | None:
    """加锁读取审批申请单。"""
    return fetch_one(conn, "SELECT * FROM approvals WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, aid))


def finalize_approval(conn: Any, aid: str, approve: bool, decided_by: str, comment: str) -> None:
    """落库审批决断结果。"""
    conn.execute(
        "UPDATE approvals SET state=%s,decided_by=%s,comment=%s,decided_at=now() WHERE id=%s",
        ("approved" if approve else "rejected", decided_by, comment[:1000], aid),
    )


def list_audit_events(conn: Any, tenant_id: str) -> list[dict]:
    """按主键倒序查询租户审计事件流水。"""
    return fetch_all(conn, "SELECT * FROM audit_events WHERE tenant_id=%s ORDER BY id DESC LIMIT 500", (tenant_id,))
