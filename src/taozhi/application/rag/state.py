"""RAG 工作流状态结构、Token 用量聚合与每节点执行租约前置守卫。"""
from __future__ import annotations

import time
from typing import Tuple, TypedDict

from taozhi.config import load_config
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence.access_repo import load_principal, sources_valid
from taozhi.infrastructure.persistence.job_repo import get_job_by_id, update_job_stage
from taozhi.knowledge.db import database_connection


class State(TypedDict, total=False):
    """企业知识问答 LangGraph 状态契约。"""

    job_id: str
    run_version: int
    question: str
    selected_agents: list
    evidence: list
    findings: list
    missing: list
    model_calls: int
    hard_block: bool
    answer: str
    sources: list
    external_blocked: bool
    business: list
    review: bool
    input_level: int
    usage: list
    history: list
    data_intent: dict
    data_result: dict
    timing: dict
    degraded: dict
    token_usage: dict


def empty_token_usage() -> dict:
    """构造空 Token 与重试统计结构体。"""
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "llm_attempts": 0,
        "llm_errors": 0,
        "failovers": 0,
        "json_retries": 0,
        "by_role": {},
    }


def merge_token_usage(base: dict | None, role_key: str, u: dict) -> dict:
    """将单次模型调用消耗并入任务级累计与按角色分桶账本。"""
    out = empty_token_usage()
    if base:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_attempts", "llm_errors", "failovers", "json_retries"):
            out[k] = int(base.get(k, 0) or 0)
        out["cost_usd"] = round(float(base.get("cost_usd", 0.0) or 0.0), 6)
        out["by_role"] = {rk: dict(rv) for rk, rv in (base.get("by_role") or {}).items()}
    pt = int(u.get("prompt_tokens", 0) or 0)
    ct = int(u.get("completion_tokens", 0) or 0)
    tt = int(u.get("total_tokens", pt + ct) or (pt + ct))
    cost = float(u.get("cost_usd", 0.0) or 0.0)
    att = int(u.get("llm_attempts", 0) or 0)
    err = int(u.get("llm_errors", 0) or 0)
    fov = int(u.get("failovers", u.get("failover_count", 0)) or 0)
    jrt = int(u.get("json_retries", 0) or 0)
    out["prompt_tokens"] += pt
    out["completion_tokens"] += ct
    out["total_tokens"] += tt
    out["cost_usd"] = round(out["cost_usd"] + cost, 6)
    out["llm_attempts"] += att
    out["llm_errors"] += err
    out["failovers"] += fov
    out["json_retries"] += jrt
    rb = out["by_role"].setdefault(
        role_key,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "llm_attempts": 0, "json_retries": 0},
    )
    rb["prompt_tokens"] += pt
    rb["completion_tokens"] += ct
    rb["total_tokens"] += tt
    rb["cost_usd"] = round(rb["cost_usd"] + cost, 6)
    rb["llm_attempts"] += att
    rb["json_retries"] += jrt
    return out


def validate_task_execution(state: State, stage: str, progress: int, external: bool = False) -> Tuple[object, dict]:
    """每个节点重验取消状态、执行租约、身份代际与外发授权，并推进阶段进度。"""
    with database_connection() as c:
        job = get_job_by_id(c, state["job_id"])
        if (
            not job
            or job["state"] != "running"
            or job["run_version"] != state["run_version"]
            or job["cancel_requested"]
            or not job["lease_until"]
            or job["lease_until"].timestamp() < time.time()
        ):
            raise Denied("任务已取消或执行租约失效", "TASK_STOPPED", 409)
        if time.time() - job["started_at"].timestamp() > load_config().task_timeout:
            raise Denied("任务处理超时，请重试", "TASK_TIMEOUT", 409)
        p = load_principal(c, job["owner_id"])
        if p.version != job["owner_version"]:
            raise Denied("人员权限已变化，请重新提交", "PERMISSION_CHANGED", 409)
        if not sources_valid(c, p, state.get("sources", []), external):
            raise Denied("资料来源或访问权限已变化", "SOURCE_CHANGED", 409)
        if external and (not job["payload"].get("allow_external", True) or job["input_level"] >= 3 and not p.boss):
            raise Denied("当前问题不允许发送外部模型", "EXTERNAL_BLOCKED", 409)
        update_job_stage(c, p.tenant_id, job["id"], job["run_version"], stage, progress)
        return p, job
