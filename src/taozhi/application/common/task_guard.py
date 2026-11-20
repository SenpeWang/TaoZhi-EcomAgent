"""工作流公共节点守卫、Token 用量聚合与 OpenTelemetry 节点级埋点包装。"""
from __future__ import annotations

import functools
import json
import time
from typing import Any, Callable, Mapping, Tuple

from taozhi.config import load_config
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence.access_repo import load_principal, sources_valid
from taozhi.infrastructure.persistence.job_repo import get_job_by_id, update_job_stage
from taozhi.knowledge.db import database_connection
from taozhi.observability.tracing import node_span


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


def validate_task_execution(state: Mapping[str, Any], stage: str, progress: int, external: bool = False) -> Tuple[object, dict]:
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


def traced_node(name: str, func: Callable[[Any], Any], span_prefix: str = "pipeline."):
    """节点级 span 包装与单节点耗时累加。"""

    @functools.wraps(func)
    def wrapper(state: Mapping[str, Any]):
        attrs = {"job_id": str(state.get("job_id", "")), "run_version": int(state.get("run_version", 0) or 0)}
        with node_span(f"{span_prefix}{name}", attrs) as span:
            t0 = time.perf_counter()
            result = func(state)
            node_ms = int((time.perf_counter() - t0) * 1000)
            if isinstance(result, dict):
                result["timing"] = {**(state.get("timing") or {}), **(result.get("timing") or {}), f"{name}_ms": node_ms}
            try:
                if span is not None:
                    r = result or {}
                    if r.get("selected_agents") is not None:
                        span.set_attribute("state.selected_agents", ",".join(map(str, r["selected_agents"])))
                    for key in ("evidence", "findings", "missing", "business"):
                        if key in r:
                            span.set_attribute("state." + key, len(r[key] or []))
                    if "answer" in r:
                        span.set_attribute("state.answer_chars", len(r.get("answer") or ""))
                    if "model_calls" in r:
                        span.set_attribute("state.model_calls", int(r.get("model_calls") or 0))
                    if r.get("usage"):
                        span.set_attribute("state.usage", json.dumps(r["usage"], ensure_ascii=False)[:600])
                    if r.get("hard_block") is not None:
                        span.set_attribute("state.hard_block", bool(r.get("hard_block")))
            except Exception:
                pass
            return result

    return wrapper
