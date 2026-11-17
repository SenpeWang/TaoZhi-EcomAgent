"""账号、组织架构、审批工作流、系统状态与审计应用服务。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

from taozhi.config import get_settings, load_config
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.passwords import hash_password, verify_password
from taozhi.infrastructure.persistence import admin_repo, document_repo, job_repo
from taozhi.infrastructure.persistence.access_repo import (
    audit,
    can_read,
    can_write,
    get_document,
    require_system,
)
from taozhi.knowledge import es_index
from taozhi.knowledge.db import database_connection
from taozhi.knowledge.embedding import available as embed_available
from taozhi.knowledge.semantic_cache import get_semantic_cache


def public_user(row: dict) -> dict[str, Any]:
    """裁剪用户行中的敏感字段，仅保留对外安全视图。"""
    return {k: row[k] for k in ("id", "username", "display_name", "system_admin", "active", "must_change", "version")}


def get_portal_info() -> dict[str, Any]:
    """查询工作台基本信息及（若开启）内网快捷登录账号列表。"""
    cfg = load_config()
    with database_connection() as c:
        t = admin_repo.find_tenant_by_code(c, cfg.tenant_code)
        data: dict[str, Any] = dict(
            name=t["name"] if t else "企业智能协作工作台",
            mode=cfg.mode,
            tenant_code=cfg.tenant_code,
        )
        if cfg.quick_login and t:
            rows = admin_repo.list_quick_login_rows(c, t["id"])
            default = cfg.quick_login_password if cfg.quick_login_passwords else ""
            creds: dict[str, Any] = {}
            if cfg.quick_login_passwords and not default:
                cred_path = cfg.runtime_dir / "accounts.json"
                try:
                    creds = json.loads(cred_path.read_text()) if cred_path.exists() else {}
                except Exception as exc:
                    _LOG.warning("读取快捷登录凭据文件失败 (%s): %s", cred_path, exc)
                    creds = {}
            result = []
            for r in rows:
                item: dict[str, Any] = dict(
                    username=r["username"],
                    display_name=r["display_name"],
                    role="admin" if r["system_admin"] else (r["roles"] or "-"),
                )
                if r["username"].startswith("syn_") and cfg.quick_eval_password:
                    item["password"] = cfg.quick_eval_password
                elif default:
                    item["password"] = default
                elif creds:
                    item["password"] = creds.get(r["username"], {}).get("password", "")
                result.append(item)
            data["quick_accounts"] = result
    return data


def revoke_user_session(p: Principal, session_hash: str) -> dict[str, str]:
    """吊销当前会话并记录退出审计。"""
    with database_connection() as c:
        admin_repo.revoke_session(c, session_hash)
        audit(c, p, "退出登录", system=True)
    return {"message": "已退出登录"}


def change_own_password(p: Principal, current_password: str, new_password: str) -> dict[str, str]:
    """校验原口令并修改当前用户自身口令，同时清理引导口令缓存。"""
    cfg = load_config()
    with database_connection() as c:
        row = admin_repo.get_user_by_id_for_update(c, p.id)
        if not row or not verify_password(current_password, row["password_hash"]):
            raise Denied("原密码不正确", "INVALID_PASSWORD", 400)
        if verify_password(new_password, row["password_hash"]):
            raise Denied("新密码不能与原密码相同", "INVALID_PASSWORD", 400)
        admin_repo.update_user_password(c, p.id, hash_password(new_password, cfg.mode), must_change=False)
        admin_repo.invalidate_authorization(c, p.tenant_id, p.id)
        audit(c, p, "修改本人密码", p.id, system=True)
    for private in cfg.runtime_dir.glob("bootstrap-*.json"):
        try:
            if json.loads(private.read_text()).get("user_id") == p.id:
                private.unlink()
        except (OSError, ValueError) as exc:
            _LOG.debug("清理历史引导密码文件跳过 (%s): %s", private, exc)
    return {"message": "密码已修改，请重新登录"}


def create_organization(p: Principal, *, name: str, kind: str, parent_id: str) -> dict:
    """在系统管理员/老板权限下新增部门或小组节点。"""
    require_system(p)
    parent = p.nodes.get(parent_id)
    if (
        not parent
        or (kind == "department" and parent["kind"] != "company")
        or (kind == "team" and parent["kind"] != "department")
    ):
        raise Denied("部门应属于公司，小组应属于部门", "INVALID_ORGANIZATION", 400)
    with database_connection() as c:
        row = admin_repo.insert_org_node(c, p.tenant_id, parent_id, name, kind)
        admin_repo.invalidate_authorization(c, p.tenant_id)
        audit(c, p, "新增组织", row["id"], system=True)
    return row


def request_organization_change(p: Principal, node_id: str, changes: dict[str, Any]) -> dict:
    """发起非公司根节点的组织调整审批申请。"""
    require_system(p)
    n = p.nodes.get(node_id)
    if not n or n["kind"] == "company":
        raise Denied("组织不存在或不能修改", "INVALID", 400)
    with database_connection() as c:
        return admin_repo.create_approval(
            c,
            p,
            "org",
            node_id,
            dict(**changes, expected_version=n["version"]),
            p.root,
        )


def list_visible_users(p: Principal) -> list[dict[str, Any]]:
    """按管理权限范围列出租户下可见用户及组织任职。"""
    if not (p.system_admin or p.boss or p.leader):
        return [p.public()]
    with database_connection() as c:
        rows = admin_repo.list_users_in_tenant(c, p.tenant_id)
        visible = []
        for u in rows:
            membership = admin_repo.list_user_memberships(c, p.tenant_id, u["id"])
            if p.system_admin or p.boss or any(p.manages(m["node_id"]) for m in membership):
                visible.append(dict(**public_user(u), memberships=membership))
        return visible


def create_user(p: Principal, *, username: str, password: str, display_name: str) -> dict[str, Any]:
    """创建待分配组织权限的企业新账号。"""
    require_system(p)
    norm_username = username.strip().casefold()
    if not re.fullmatch(r"[\w.-]{3,64}", norm_username):
        raise Denied("账号须为 3–64 位字母、数字、中文或 ._-", "INVALID_ACCOUNT", 400)
    cfg = load_config()
    with database_connection() as c:
        if admin_repo.user_exists_by_username(c, p.tenant_id, norm_username):
            raise Denied("账号已存在", "CONFLICT", 409)
        row = admin_repo.insert_user(
            c,
            tenant_id=p.tenant_id,
            username=norm_username,
            display_name=display_name.strip() or norm_username,
            password_hash=hash_password(password, cfg.mode),
            must_change=cfg.mode == "production",
        )
        audit(c, p, "创建待分配账号", row["id"], system=True)
    return public_user(row)


def request_user_change(p: Principal, target_user_id: str, payload: dict[str, Any]) -> dict:
    """发起账号信息、启停状态或重置口令审批申请。"""
    require_system(p)
    cfg = load_config()
    with database_connection() as c:
        u = admin_repo.get_user(c, p.tenant_id, target_user_id)
        if not u:
            raise Denied("账号不存在", "NOT_FOUND", 404)
        clean: dict[str, Any] = {
            k: v for k, v in payload.items() if k in ("active", "display_name", "system_admin") and v is not None
        }
        if payload.get("new_password"):
            clean["password_hash"] = hash_password(payload["new_password"], cfg.mode)
        clean["expected_version"] = u["version"]
        return admin_repo.create_approval(c, p, "user", target_user_id, clean, p.root, "boss")


def request_membership_change(
    p: Principal,
    target_user_id: str,
    *,
    node_id: str,
    role: str,
    remove: bool = False,
) -> dict:
    """发起组织角色授予或解除审批申请。"""
    require_system(p)
    with database_connection() as c:
        u = admin_repo.get_user(c, p.tenant_id, target_user_id)
        n = p.nodes.get(node_id)
        if not u or not n or role not in ("employee", "leader", "boss"):
            raise Denied("人员或组织无效", "INVALID", 400)
        if role == "boss" and n["kind"] != "company":
            raise Denied("老板身份只能绑定公司", "INVALID", 400)
        required = "boss" if role != "employee" or target_user_id == p.id or n["kind"] == "company" else "leader"
        return admin_repo.create_approval(
            c,
            p,
            "membership",
            target_user_id,
            dict(node_id=node_id, role=role, remove=remove, expected_version=u["version"]),
            node_id,
            required,
        )


def _ensure_last_governance_holder(
    conn: Any,
    tenant_id: str,
    user_id: str,
    *,
    remove_role: str | None = None,
    disable: bool = False,
    remove_admin: bool = False,
) -> None:
    """兜底校验：禁止停用或移除租户下最后一位老板或系统管理员。"""
    if disable or remove_role == "boss":
        bosses = admin_repo.list_active_company_bosses(conn, tenant_id)
        if len(bosses) <= 1 and any(x["id"] == user_id for x in bosses):
            raise Denied("不能停用或移除最后一位老板", "LAST_OWNER", 409)
    if disable or remove_admin:
        admins = admin_repo.list_active_system_admins(conn, tenant_id)
        if len(admins) <= 1 and any(x["id"] == user_id for x in admins):
            raise Denied("不能停用或移除最后一位管理员", "LAST_ADMIN", 409)


def list_approvals_for_principal(p: Principal) -> list[dict[str, Any]]:
    """列出当前用户有审批权、申请权或管理员可见的审批申请单。"""
    kind_labels = {
        "membership": "组织任职",
        "user": "账号变更",
        "document_policy": "资料权限",
        "grant": "单文档授权",
        "publish": "资料发布",
        "org": "组织调整",
    }
    with database_connection() as c:
        result = []
        for a in admin_repo.list_approvals(c, p.tenant_id):
            responsible = p.boss or (
                a["required_role"] == "leader" and p.manages(a["node_id"]) and p.id != a["requested_by"]
            )
            if not (
                responsible
                or a["requested_by"] == p.id
                or (p.system_admin and a["kind"] in ("membership", "user", "org"))
            ):
                continue
            d = document_repo.find_document_any(c, p.tenant_id, a["target_id"])
            title = d["title"] if d and can_read(c, p, d, True) else ""
            payload = {k: v for k, v in a["payload"].items() if k != "password_hash"}
            if "password_hash" in a["payload"]:
                payload["reset_password"] = True
            result.append(
                dict(
                    **{
                        k: a[k]
                        for k in ("id", "kind", "target_id", "requested_by", "required_role", "state", "created_at", "comment")
                    },
                    label=kind_labels.get(a["kind"], "业务申请"),
                    title=title,
                    can_decide=responsible,
                    payload=payload,
                )
            )
        return result


def decide_approval(p: Principal, aid: str, *, approve: bool, comment: str) -> dict[str, str]:
    """在单事务中校验并发乐观锁并执行审批通过或拒绝落库。"""
    is_prod = load_config().mode == "production"
    with database_connection() as c:
        a = admin_repo.get_approval_for_update(c, p.tenant_id, aid)
        if not a:
            raise Denied("申请不存在", "NOT_FOUND", 404)
        if a["state"] != "pending":
            raise Denied("申请已经处理", "CONFLICT", 409)
        if not p.boss and (a["required_role"] == "boss" or not p.manages(a["node_id"]) or p.id == a["requested_by"]):
            raise Denied("此申请需要对应业务负责人审批")
        v = a["payload"]
        kind = a["kind"]
        if approve:
            if kind in ("membership", "user"):
                u = admin_repo.get_user(c, p.tenant_id, a["target_id"], for_update=True)
                if not u or u["version"] != v["expected_version"]:
                    raise Denied("人员状态已变化，请重新申请", "STALE", 409)
                if kind == "membership":
                    if v["role"] != "employee" and not p.boss:
                        raise Denied()
                    if v["remove"]:
                        _ensure_last_governance_holder(c, p.tenant_id, u["id"], remove_role=v["role"])
                    admin_repo.upsert_or_delete_membership(
                        c, p.tenant_id, u["id"], v["node_id"], v["role"], remove=bool(v["remove"])
                    )
                else:
                    _ensure_last_governance_holder(
                        c,
                        p.tenant_id,
                        u["id"],
                        disable=v.get("active") is False,
                        remove_admin=v.get("system_admin") is False,
                    )
                    admin_repo.update_user_fields(c, p.tenant_id, u["id"], v, is_production=is_prod)
                admin_repo.invalidate_authorization(c, p.tenant_id, u["id"])
            elif kind in ("document_policy", "grant", "publish"):
                d = get_document(c, p, a["target_id"])
                document_repo.lock_document(c, d["id"])
                d = get_document(c, p, d["id"])
                if d["acl_version"] != v["expected_acl"] or d["current_version"] != v["expected_version"]:
                    raise Denied("资料版本或权限已变化，请重新申请", "STALE", 409)
                if kind == "publish":
                    if not can_write(c, p, d) or (not p.boss and (d["level"] > 2 or a["requested_by"] == p.id)):
                        raise Denied("需要另一位负责人审核发布")
                    if d["state"] != "review" or not document_repo.has_version_chunks(c, d["id"], d["current_version"]):
                        raise Denied("资料尚未完成解析", "NOT_READY", 409)
                    document_repo.publish_document_state(c, d["id"])
                    try:
                        if es_index.available():
                            rows = document_repo.list_version_chunks_for_index(c, d["id"], d["current_version"])
                            es_index.delete_chunks(d["id"])
                            es_index.index_chunks(rows)
                    except Exception as exc:
                        print("ES 发布索引同步失败", type(exc).__name__, flush=True)
                elif kind == "grant":
                    if not p.boss:
                        raise Denied()
                    target = admin_repo.get_user(c, p.tenant_id, v["user_id"], active_only=True)
                    if not target:
                        raise Denied("授权人员已失效", "STALE", 409)
                    document_repo.upsert_or_revoke_grant(
                        c,
                        tenant_id=p.tenant_id,
                        doc_id=d["id"],
                        target_user_id=target["id"],
                        granted_by=p.id,
                        payload=v,
                    )
                else:
                    if not p.boss:
                        raise Denied()
                    document_repo.update_document_policy_fields(c, d["id"], v)
                admin_repo.invalidate_authorization(c, p.tenant_id)
                get_semantic_cache().invalidate(p.tenant_id, document_id=d["id"])
            elif kind == "org":
                if not p.boss:
                    raise Denied()
                n = admin_repo.get_org_node_for_update(c, p.tenant_id, a["target_id"])
                if not n or n["version"] != v["expected_version"]:
                    raise Denied("组织状态已变化", "STALE", 409)
                parent = v.get("parent_id", n["parent_id"])
                if parent and (parent not in p.nodes or p.covers(n["id"], parent)):
                    raise Denied("组织不能形成循环", "INVALID", 400)
                if n["kind"] == "company":
                    raise Denied("公司根节点不能移动或停用", "INVALID", 400)
                parent_node = p.nodes.get(parent)
                if (
                    not parent_node
                    or (n["kind"] == "department" and parent_node["kind"] != "company")
                    or (n["kind"] == "team" and parent_node["kind"] != "department")
                ):
                    raise Denied("部门必须属于公司，小组必须属于部门", "INVALID", 400)
                if v.get("active") is False and admin_repo.has_active_child_org(c, n["id"]):
                    raise Denied("请先处理下级组织", "CONFLICT", 409)
                admin_repo.update_org_node(
                    c, n["id"], v.get("name", n["name"]), parent, v.get("active", n["active"])
                )
                for uid in admin_repo.list_distinct_member_user_ids(c, p.tenant_id):
                    admin_repo.invalidate_authorization(c, p.tenant_id, uid)
            else:
                raise Denied("申请类型不支持", "INVALID", 400)
        admin_repo.finalize_approval(c, aid, approve, p.id, comment)
        audit(
            c,
            p,
            "批准申请" if approve else "拒绝申请",
            a["target_id"],
            a["node_id"],
            system=kind in ("membership", "user", "org"),
            detail={"kind": kind, "decision": "approved" if approve else "rejected"},
        )
    return {"message": "申请已批准" if approve else "申请已拒绝"}


def get_system_status(p: Principal) -> dict[str, Any]:
    """系统管理员/老板查看数据库、Worker、模型与业务接口可用状态。"""
    require_system(p)
    with database_connection() as c:
        counts = job_repo.count_jobs_by_state(c, p.tenant_id)
        workers = job_repo.count_active_workers(c)
    s = get_settings()
    return dict(
        database=True,
        worker_available=workers > 0,
        counts=counts,
        model_configured=bool(s.api_key),
        business_configured=bool(s.business_api_url and s.business_api_token),
        semantic_retrieval=embed_available(),
        mode=load_config().mode,
    )


def get_health_status() -> dict[str, str]:
    """检查数据库连通性与运行中任务状态。"""
    with database_connection() as c:
        admin_repo.find_tenant_by_code(c, load_config().tenant_code)
        job_repo.count_running_workers(c)
    return dict(status="ok", version="3.0", message="服务正常")


def list_audit_events_for_principal(p: Principal) -> list[dict[str, Any]]:
    """按角色密级与组织管理范围过滤安全审计日志。"""
    if not (p.system_admin or p.boss or p.leader):
        raise Denied()
    with database_connection() as c:
        rows = admin_repo.list_audit_events(c, p.tenant_id)
        return [
            r
            for r in rows
            if (r["system"] and (p.system_admin or p.boss))
            or (not r["system"] and p.grade(r["node_id"]) >= r["level"] and p.manages(r["node_id"]))
        ]
