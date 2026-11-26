"""任务提交、查询、取消、重试和人工审核。"""
from __future__ import annotations
from taozhi.domain.roles import is_system_admin

from typing import Any

from taozhi.domain.access import can_review, can_task
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence import job_repo, research_task_repo
from taozhi.infrastructure.persistence.access_repo import audit, batch_sources_validity, load_principal, sources_valid
from taozhi.infrastructure.persistence.database import database_connection


def submit_task(
    p: Principal,
    *,
    question: str,
    kind: str,
    node_id: str,
    conversation_id: str,
    payload_data: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """提交智能问答或自主协同调研任务，强制由服务端判定密级上限。"""
    node = node_id or next((m["node_id"] for m in p.memberships if m["role"] in ("leader", "employee")), p.root)
    input_level = 3 if p.boss else (2 if p.leader else 1)
    with database_connection() as c:
        job = job_repo.create_job(
            c,
            p,
            kind,
            node,
            payload_data,
            question,
            input_level,
            idempotency_key,
            conversation_id=conversation_id,
        )
        return {"id": job["id"], "task_id": job["id"], "state": job["state"], "message": "任务已提交"}


def list_tasks(p: Principal, kind: str = "") -> list[dict[str, Any]]:
    """列出当前用户有权限查看的问答与调研任务（批量预检验来源以消除 N+1 查询）。"""
    with database_connection() as c:
        rows = job_repo.list_ask_and_research_jobs(c, p.tenant_id, kind=kind)
        permitted = [r for r in rows if can_task(p, r)]
        validity = batch_sources_validity(c, p, permitted)
        return [
            job_repo.present_job(c, p, r, False, valid_override=validity.get(r["id"], False))
            for r in permitted
            if r["owner_id"] == p.id or validity.get(r["id"], False)
        ]


def get_task(p: Principal, task_id: str) -> dict[str, Any]:
    """按编号读取任务详情及完整事件流。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        data = job_repo.present_job(c, p, r)
        if r.get("kind") == "research" and isinstance(data.get("result"), dict):
            rows = research_task_repo.list_tasks(
                c, tenant_id=p.tenant_id, job_id=task_id, run_version=int(r["run_version"])
            )
            for row in rows:
                row["comments"] = research_task_repo.list_comments(c, row["id"])
            data["result"]["research_tasks"] = rows
        return data


def list_research_tasks(p: Principal, task_id: str) -> dict[str, Any]:
    """读取某次调研执行的持久任务板与评论。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id)
        if not r or not can_task(p, r):
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        rows = research_task_repo.list_tasks(
            c, tenant_id=p.tenant_id, job_id=task_id, run_version=int(r["run_version"])
        )
        for row in rows:
            row["comments"] = research_task_repo.list_comments(c, row["id"])
        return {"items": rows}


def cancel_task(p: Principal, task_id: str) -> dict[str, str]:
    """请求取消正在排队、执行或待审核中的任务。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r or not (r["owner_id"] == p.id or p.boss):
            raise Denied()
        if r["state"] not in ("queued", "running", "review"):
            raise Denied("任务已经结束", "CONFLICT", 409)
        job_repo.mark_job_cancelled(c, task_id)
        audit(c, p, "取消任务", task_id, r["node_id"], r["input_level"])
    return {"message": "已请求取消"}


def retry_task(p: Principal, task_id: str) -> dict[str, Any]:
    """递增执行版本号并重新投递任务入队。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        if not (r["owner_id"] == p.id or p.boss or is_system_admin(p.role_level)):
            raise Denied("无权操作此任务", "FORBIDDEN", 403)
        if r["state"] == "running":
            raise Denied("任务正在执行中，无需重复发起", "CONFLICT", 409)
        new_ver = (r["run_version"] or 1) + 1
        job_repo.requeue_job(c, p.tenant_id, task_id, new_ver)
        audit(c, p, "重新发起任务", task_id, r["node_id"], r["input_level"])
    return {"message": "任务已重新排队执行", "task_id": task_id, "run_version": new_ver}


def review_task(p: Principal, task_id: str, *, approve: bool, comment: str) -> dict[str, str]:
    """审核待人工复核的业务答案并校验双方权限及来源时效。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r or r["state"] != "review":
            raise Denied("任务不在待审核状态", "CONFLICT", 409)
        if not can_review(p, r) or (r["owner_id"] == p.id and not p.boss):
            raise Denied("需要对应范围的另一位负责人审核")
        owner = load_principal(c, r["owner_id"])
        if not sources_valid(c, p, r["sources"]) or not sources_valid(c, owner, r["sources"]):
            raise Denied("来源或权限已变化，请重新查询", "STALE", 409)
        if approve and (r["result"] or {}).get("hard_block"):
            raise Denied("缺少可靠来源，不能审核为事实", "QUALITY_BLOCK", 409)
        job_repo.save_task_review(c, p.tenant_id, r["id"], p.id, approve, comment)
        audit(
            c,
            p,
            "审核业务答案",
            r["id"],
            r["node_id"],
            r["input_level"],
            detail={"decision": "approved" if approve else "rejected"},
        )
    return {"message": "答案已发布" if approve else "答案未通过审核"}
