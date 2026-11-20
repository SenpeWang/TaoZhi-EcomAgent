"""从已持久化的问答、调研任务和运行账本读取租户轨迹。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from taozhi.knowledge.db import fetch_all, fetch_one


_TRACE_COLUMNS = """
    j.id, j.kind, j.question, j.state, j.stage, j.progress, j.owner_id,
    u.username, u.display_name, n.name AS node_name, j.attempts,
    j.created_at, j.started_at, j.finished_at
"""
_TRACE_FROM = """
    FROM jobs j
    JOIN users u ON (u.tenant_id, u.id) = (j.tenant_id, j.owner_id)
    JOIN org_nodes n ON (n.tenant_id, n.id) = (j.tenant_id, j.node_id)
"""
_TRACE_SCOPE = "WHERE j.tenant_id=%s AND j.kind IN ('ask','research')"


def list_traces(
    conn: Any,
    tenant_id: str,
    *,
    kind: str | None,
    state: str | None,
    query: str,
    before: tuple[datetime, str] | None,
    limit: int,
) -> list[dict]:
    """按创建时间与任务编号倒序翻页，查询包含已停用提交人和历史组织的任务。"""
    trace_id = query.lower() if re.fullmatch(r"[0-9a-fA-F]{32}", query) else None
    matched = None
    if trace_id:
        matched = fetch_one(
            conn,
            "SELECT job_id, run_version FROM task_spans WHERE tenant_id=%s AND trace_id=%s LIMIT 1",
            (tenant_id, trace_id),
        )
    matched_version = "%s" if matched else "NULL"
    clauses = [f"SELECT {_TRACE_COLUMNS}, {matched_version} AS matched_run_version {_TRACE_FROM} {_TRACE_SCOPE}"]
    params: list[Any] = [matched["run_version"], tenant_id] if matched else [tenant_id]
    if kind:
        clauses.append("AND j.kind = %s")
        params.append(kind)
    if state:
        clauses.append("AND j.state = %s")
        params.append(state)
    if matched:
        clauses.append("AND j.id=%s")
        params.append(matched["job_id"])
    elif query:
        escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        pattern = f"%{escaped}%"
        search = (
            "j.id = %s OR j.question ILIKE %s ESCAPE '!' OR u.username ILIKE %s ESCAPE '!' "
            "OR u.display_name ILIKE %s ESCAPE '!'"
        )
        params.extend((query, pattern, pattern, pattern))
        if trace_id:
            search += " OR (j.state IN ('review','completed','rejected') AND j.result->>'trace_id'=%s)"
            params.append(trace_id)
        clauses.append(f"AND ({search})")
    if before:
        clauses.append("AND (j.created_at, j.id) < (%s, %s)")
        params.extend(before)
    clauses.append("ORDER BY j.created_at DESC, j.id DESC LIMIT %s")
    params.append(limit)
    return fetch_all(conn, " ".join(clauses), tuple(params))


def get_trace(conn: Any, tenant_id: str, task_id: str) -> dict | None:
    """读取当前执行的内建链路编号，历史任务可从原有结果中取回关联编号。"""
    return fetch_one(
        conn,
        f"SELECT {_TRACE_COLUMNS}, COALESCE(("
        "SELECT s.trace_id FROM task_spans s WHERE s.tenant_id=j.tenant_id AND s.job_id=j.id "
        "AND s.run_version=j.run_version ORDER BY s.id LIMIT 1), "
        f"CASE WHEN j.state IN ('review', 'completed', 'rejected') THEN j.result->>'trace_id' END"
        f") AS trace_id {_TRACE_FROM} {_TRACE_SCOPE} AND j.id = %s",
        (tenant_id, task_id),
    )


def get_span_job(conn: Any, tenant_id: str, task_id: str, run_version: int | None = None) -> dict | None:
    """验证任务和可选执行版本归属，并读取当前版本以识别租约接管后的未关闭节点。"""
    sql = (
        "SELECT j.run_version, j.state, j.lease_until, j.finished_at, clock_timestamp() AS observed_at FROM jobs j "
        "WHERE j.tenant_id=%s AND j.id=%s AND j.kind IN ('ask','research')"
    )
    if run_version is None:
        return fetch_one(conn, sql, (tenant_id, task_id))
    return fetch_one(
        conn, sql + " AND EXISTS (SELECT 1 FROM task_spans s WHERE s.tenant_id=j.tenant_id "
        "AND s.job_id=j.id AND s.run_version=%s)", (tenant_id, task_id, run_version),
    )


def list_span_runs(conn: Any, tenant_id: str, task_id: str, before_version: int | None, limit: int) -> list[dict]:
    """按执行版本聚合 Span，根节点状态反映整体结果，包括子节点失败后恢复的执行。"""
    boundary = "" if before_version is None else " AND run_version<%s"
    params = (tenant_id, task_id) if before_version is None else (tenant_id, task_id, before_version)
    return fetch_all(
        conn,
        "SELECT run_version, MIN(trace_id) AS trace_id, MIN(started_at) AS started_at, "
        "MAX(ended_at) FILTER (WHERE parent_span_id IS NULL) AS ended_at, "
        "MAX(status) FILTER (WHERE parent_span_id IS NULL) AS status, COUNT(*) AS span_count, "
        "COUNT(*) FILTER (WHERE status='error') AS failed_count, "
        "COALESCE(SUM(TRUNC((attributes->>'gen_ai.usage.input_tokens')::numeric)) "
        "FILTER (WHERE kind='LLM' AND jsonb_typeof(attributes->'gen_ai.usage.input_tokens')='number'), 0) AS input_tokens, "
        "COALESCE(SUM(TRUNC((attributes->>'gen_ai.usage.output_tokens')::numeric)) "
        "FILTER (WHERE kind='LLM' AND jsonb_typeof(attributes->'gen_ai.usage.output_tokens')='number'), 0) AS output_tokens "
        f"FROM task_spans WHERE tenant_id=%s AND job_id=%s{boundary} "
        "GROUP BY run_version ORDER BY run_version DESC LIMIT %s",
        (*params, limit),
    )


def list_spans(
    conn: Any, tenant_id: str, task_id: str, run_version: int, after_id: int, limit: int,
) -> list[dict]:
    """按创建游标获取该执行中的节点；节点结束时更新原记录，父子标识不变。"""
    return fetch_all(
        conn,
        "SELECT id, trace_id, span_id, parent_span_id, name, kind, started_at, ended_at, "
        "duration_ms, status, error_type, attributes FROM task_spans "
        "WHERE tenant_id=%s AND job_id=%s AND run_version=%s AND id>%s ORDER BY id LIMIT %s",
        (tenant_id, task_id, run_version, after_id, limit),
    )


def list_events(conn: Any, tenant_id: str, task_id: str, after_id: int, limit: int) -> list[dict]:
    """按稳定事件编号读取一页时间线，保留执行代际以区分重试。"""
    return fetch_all(
        conn,
        "SELECT id, run_version, stage, progress, status, created_at FROM task_events "
        "WHERE tenant_id=%s AND job_id=%s AND id>%s ORDER BY id LIMIT %s",
        (tenant_id, task_id, after_id, limit),
    )


def list_model_calls(
    conn: Any, tenant_id: str, task_id: str, before_id: int | None, limit: int,
) -> list[dict]:
    """按调用编号向前翻页，优先读取最新调用及其当前用量。"""
    boundary = "" if before_id is None else " AND id<%s"
    params = (tenant_id, task_id) if before_id is None else (tenant_id, task_id, before_id)
    return fetch_all(
        conn,
        "SELECT id, role, elapsed_ms, prompt_tokens, completion_tokens, outcome, created_at FROM model_calls "
        f"WHERE tenant_id=%s AND job_id=%s{boundary} ORDER BY id DESC LIMIT %s",
        (*params, limit),
    )
