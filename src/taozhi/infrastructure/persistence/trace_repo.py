"""从已持久化的问答、调研任务和 OpenTelemetry 节点读取租户轨迹。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from taozhi.domain.trace_activity import CHAIN_CATEGORY_BY_NAME
from taozhi.knowledge.db import fetch_all, fetch_one


_ACTIVITY_CATEGORY = "CASE WHEN kind='CHAIN' THEN COALESCE((%s::jsonb)->>name,kind) ELSE kind END"
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

def _job_scope(tenant_id: str, owner_id: str | None) -> tuple[str, tuple]:
    """生成所有轨迹入口共用的租户及可见发起人条件。"""
    if owner_id is None:
        return _TRACE_SCOPE, (tenant_id,)
    return _TRACE_SCOPE + " AND j.owner_id=%s", (tenant_id, owner_id)


def _contains(query: str) -> str:
    """按字面量匹配查询词，保留用户输入的百分号和下划线。"""
    return "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def list_owners(conn: Any, tenant_id: str, *, query: str, after: str | None, limit: int) -> list[dict]:
    """以租户内唯一的登录名分页，包括历史提问对应的停用账号。"""
    clauses = ["SELECT id, username, display_name FROM users WHERE tenant_id=%s"]
    params = [tenant_id]
    if query:
        pattern = _contains(query)
        clauses.append("AND (username ILIKE %s ESCAPE '!' OR display_name ILIKE %s ESCAPE '!')")
        params.extend((pattern, pattern))
    if after is not None:
        clauses.append("AND username>%s")
        params.append(after)
    clauses.append("ORDER BY username LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def list_traces(
    conn: Any,
    tenant_id: str,
    *,
    owner_id: str | None,
    kind: str | None,
    state: str | None,
    query: str,
    before: tuple[datetime, str] | None,
    limit: int,
    include_sources: bool = False,
) -> list[dict]:
    """按创建时间与任务编号倒序翻页，查询包含已停用提交人和历史组织的任务。"""
    scope, scope_params = _job_scope(tenant_id, owner_id)
    trace_id = query.lower() if re.fullmatch(r"[0-9a-fA-F]{32}", query) else None
    params: list[Any] = [trace_id]
    metadata_match = "TRUE"
    if query and not trace_id:
        pattern = _contains(query)
        metadata_match = "j.id=%s OR u.username ILIKE %s ESCAPE '!' OR u.display_name ILIKE %s ESCAPE '!'"
        params.extend((query, pattern, pattern))
    sources = ", j.sources" if include_sources else ""
    clauses = [
        f"SELECT * FROM (SELECT {_TRACE_COLUMNS}{sources}, %s::text AS matched_trace_id, "
        f"({metadata_match}) AS matches_metadata {_TRACE_FROM} {scope}"
    ]
    params.extend(scope_params)
    if kind:
        clauses.append("AND j.kind = %s")
        params.append(kind)
    if state:
        clauses.append("AND j.state = %s")
        params.append(state)
    if trace_id:
        clauses.append("AND EXISTS (SELECT 1 FROM task_spans s WHERE s.tenant_id=j.tenant_id "
                       "AND s.job_id=j.id AND s.trace_id=%s)")
        params.append(trace_id)
    if before:
        clauses.append("AND (j.created_at, j.id) < (%s, %s)")
        params.extend(before)
    clauses.append(") AS candidates")
    if query and not trace_id:
        clauses.append("WHERE matches_metadata OR question ILIKE %s ESCAPE '!'")
        params.append(_contains(query))
    clauses.append("ORDER BY created_at DESC, id DESC LIMIT %s")
    params.append(limit)
    return fetch_all(conn, " ".join(clauses), tuple(params))


def get_trace(conn: Any, tenant_id: str, task_id: str, *, owner_id: str | None) -> dict | None:
    """读取提问及其归属，链路编号由实际采集的节点确定。"""
    scope, params = _job_scope(tenant_id, owner_id)
    sources = ", j.sources" if owner_id is not None else ""
    return fetch_one(conn, f"SELECT {_TRACE_COLUMNS}{sources} {_TRACE_FROM} {scope} AND j.id=%s", (*params, task_id))


def get_span_job(
    conn: Any, tenant_id: str, task_id: str, *, owner_id: str | None, trace_id: str | None = None,
) -> dict | None:
    """验证任务与链路归属，同时读取执行租约和节点所属版本。"""
    scope, params = _job_scope(tenant_id, owner_id)
    fields = "j.run_version, j.state, j.lease_until, j.finished_at, clock_timestamp() AS observed_at"
    if trace_id is None:
        return fetch_one(conn, f"SELECT {fields} FROM jobs j {scope} AND j.id=%s", (*params, task_id))
    return fetch_one(
        conn, f"SELECT {fields}, s.run_version AS span_version FROM jobs j JOIN task_spans s "
        f"ON (s.tenant_id,s.job_id)=(j.tenant_id,j.id) {scope} AND j.id=%s AND s.trace_id=%s LIMIT 1",
        (*params, task_id, trace_id),
    )


def list_span_runs(
    conn: Any, tenant_id: str, task_id: str, *, before: tuple[int, str] | None, trace_id: str | None, limit: int,
) -> list[dict]:
    """按实际 Trace ID 分组，每条链路独立计算节点分类、根状态与模型用量。"""
    filters = ["tenant_id=%s", "job_id=%s"]
    params: list[Any] = [tenant_id, task_id]
    if before:
        filters.append("(run_version, trace_id)<(%s,%s)")
        params.extend(before)
    if trace_id:
        filters.append("trace_id=%s")
        params.append(trace_id)
    return fetch_all(conn, f"""
        WITH page AS (
            SELECT tenant_id,job_id,run_version,trace_id FROM task_spans
            WHERE {' AND '.join(filters)}
            GROUP BY tenant_id,job_id,run_version,trace_id ORDER BY run_version DESC,trace_id DESC LIMIT %s
        ), diagnosis AS (
            SELECT run_version,trace_id,jsonb_object_agg(grouped.category,grouped.entries) AS counts
            FROM (
                SELECT run_version,trace_id,entry->>'category' AS category,COUNT(*) AS entries
                FROM page JOIN task_spans USING (tenant_id,job_id,run_version,trace_id)
                CROSS JOIN LATERAL jsonb_array_elements(diagnostics) AS entry
                GROUP BY run_version,trace_id,entry->>'category'
            ) grouped GROUP BY run_version,trace_id
        ), by_category AS (
            SELECT run_version,trace_id,kind,{_ACTIVITY_CATEGORY} AS category, COUNT(*) AS nodes, MIN(started_at) AS started_at,
                MAX(ended_at) FILTER (WHERE parent_span_id IS NULL) AS root_end,
                MAX(status) FILTER (WHERE parent_span_id IS NULL) AS root_status,
                COUNT(*) FILTER (WHERE status='error') AS errors,
                SUM(TRUNC((attributes->>'llm.token_count.prompt')::numeric)) FILTER
                    (WHERE kind='LLM' AND jsonb_typeof(attributes->'llm.token_count.prompt')='number') AS prompt_tokens,
                SUM(TRUNC((attributes->>'llm.token_count.completion')::numeric)) FILTER
                    (WHERE kind='LLM' AND jsonb_typeof(attributes->'llm.token_count.completion')='number') AS completion_tokens
            FROM page JOIN task_spans USING (tenant_id,job_id,run_version,trace_id)
            GROUP BY run_version,trace_id,kind,category
        ), totals AS (
            SELECT by_category.*,SUM(nodes) OVER (PARTITION BY run_version,trace_id,kind) AS kind_nodes
            FROM by_category
        )
        SELECT run_version,trace_id,MIN(started_at) AS started_at,MAX(root_end) AS ended_at,
            MAX(root_status) AS status,SUM(nodes) AS span_count,jsonb_object_agg(kind,kind_nodes) AS kind_counts,
            jsonb_object_agg(category,nodes) AS category_counts,
            SUM(errors) AS failed_count,COALESCE(SUM(prompt_tokens),0) AS input_tokens,
            COALESCE(SUM(completion_tokens),0) AS output_tokens,
            COALESCE(diagnosis.counts,'{{}}'::jsonb) AS diagnostic_counts
        FROM totals LEFT JOIN diagnosis USING (run_version,trace_id)
        GROUP BY run_version,trace_id,diagnosis.counts ORDER BY run_version DESC,trace_id DESC
    """, (*params, limit, Jsonb(CHAIN_CATEGORY_BY_NAME)))


def list_spans(
    conn: Any, tenant_id: str, task_id: str, trace_id: str, *, kind: str | None, after_id: int, limit: int,
) -> list[dict]:
    """按采集游标读取这条链路中的节点，类型筛选在分页之前执行。"""
    kind_filter = " AND kind=%s" if kind else ""
    params = (tenant_id, task_id, trace_id, after_id)
    return fetch_all(
        conn, "SELECT id, trace_id, span_id, parent_span_id, name, kind, started_at, ended_at, "
        "duration_ms, status, error_type, attributes, diagnostics FROM task_spans "
        f"WHERE tenant_id=%s AND job_id=%s AND trace_id=%s AND id>%s{kind_filter} ORDER BY id LIMIT %s",
        (*params, kind, limit) if kind else (*params, limit),
    )


def list_activities(
    conn: Any, tenant_id: str, task_id: str, trace_id: str, run_version: int, *,
    category: str | None, diagnostic: str | None, key_steps: tuple[str, ...], after: tuple[datetime, int] | None, limit: int,
) -> list[dict]:
    """按实际开始时刻取一页，并在同一快照中从完整父链解析每条活动的执行者。"""
    filters = ["tenant_id=%s", "job_id=%s", "trace_id=%s", "run_version=%s"]
    params: list[Any] = [tenant_id, task_id, trace_id, run_version]
    if category:
        filters.append(f"({_ACTIVITY_CATEGORY})=%s")
        params.extend([Jsonb(CHAIN_CATEGORY_BY_NAME), category])
    elif not diagnostic:
        filters.append("(kind IN ('AGENT','TOOL','LLM','RETRIEVER','EMBEDDING','RERANKER') "
                       "OR diagnostics <> '[]'::jsonb OR (kind='CHAIN' AND name=ANY(%s)))")
        params.append(list(key_steps))
    if diagnostic == "all":
        filters.append("diagnostics <> '[]'::jsonb")
    elif diagnostic:
        filters.append("diagnostics @> %s")
        params.append(Jsonb([{"category": diagnostic}]))
    if after:
        filters.append("(started_at,id)>(%s,%s)")
        params.extend(after)

    return fetch_all(conn, f"""
        WITH page AS (
            SELECT * FROM task_spans WHERE {' AND '.join(filters)}
            ORDER BY started_at,id LIMIT %s
        )
        SELECT page.id,page.trace_id,page.span_id,page.parent_span_id,page.name,page.kind,
            page.started_at,page.ended_at,page.duration_ms,page.status,page.error_type,page.attributes,page.diagnostics,
            actor.span_id AS actor_span_id,actor.kind AS actor_kind,actor.name AS actor_operation,
            actor.role AS actor_role
        FROM page LEFT JOIN LATERAL (
            WITH RECURSIVE lineage AS (
                SELECT page.span_id,page.parent_span_id,page.kind,page.name,
                    page.attributes->>'agent.name' AS role,ARRAY[page.id] AS visited
                UNION ALL
                SELECT parent.span_id,parent.parent_span_id,parent.kind,parent.name,
                    parent.attributes->>'agent.name',array_append(child.visited,parent.id)
                FROM lineage child JOIN task_spans parent ON parent.span_id=child.parent_span_id
                WHERE child.kind<>'AGENT' AND NOT (parent.id=ANY(child.visited))
                  AND (parent.tenant_id,parent.job_id,parent.trace_id,parent.run_version)
                    =(page.tenant_id,page.job_id,page.trace_id,page.run_version)
            )
            SELECT span_id,kind,name,role FROM lineage
            WHERE kind='AGENT' OR (kind='CHAIN' AND EXISTS (SELECT 1 FROM lineage WHERE parent_span_id IS NULL))
            ORDER BY (kind='AGENT') DESC, (span_id=page.span_id), cardinality(visited)
            LIMIT 1
        ) actor ON TRUE
        ORDER BY page.started_at,page.id
    """, (*params, limit))


def list_events(conn: Any, tenant_id: str, task_id: str, after_id: int, limit: int) -> list[dict]:
    """按稳定事件编号读取一页时间线，保留执行代际以区分重试。"""
    return fetch_all(
        conn,
        "SELECT id, run_version, stage, progress, status, created_at FROM task_events "
        "WHERE tenant_id=%s AND job_id=%s AND event_type='stage' AND id>%s ORDER BY id LIMIT %s",
        (tenant_id, task_id, after_id, limit),
    )
