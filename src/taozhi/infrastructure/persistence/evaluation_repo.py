"""租户范围内的评测批次与用例读写。"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from taozhi.infrastructure.persistence.database import fetch_all, fetch_one


_RUN = """
    SELECT r.run_id, r.owner_id, u.display_name AS owner_name, r.created_at, r.mode, r.git_sha,
           r.dataset_sha256, r.judges_sha256, r.gate_passed, r.passed_cases, r.failed_cases, r.unassessable_cases,
           r.metrics->'provenance'->>'transcript_sha256' AS transcript_sha256,
           r.metrics->'provenance'->>'counterexamples_sha256' AS counterexamples_sha256,
           r.metrics->'provenance'->>'model_config_sha256' AS model_config_sha256,
           r.passed_cases+r.failed_cases+r.unassessable_cases AS total_cases, r.metrics, r.report_sha256, r.baseline_sha256,
           r.dataset_id, d.name AS dataset_name, r.eval_kind, r.profile_id, p.version_tag AS profile_version_tag,
           r.pass_rate, r.mean_score, r.latency_p50_ms, r.latency_p95_ms,
           r.total_tokens, r.judges
    FROM eval_runs r JOIN users u ON (u.tenant_id,u.id)=(r.tenant_id,r.owner_id)
    LEFT JOIN eval_datasets d ON d.dataset_id=r.dataset_id
    LEFT JOIN eval_profiles p ON p.profile_id=r.profile_id
    WHERE r.tenant_id=%s
"""
_CASE = "case_id,name,category,suite,status,score,hard_failure"


def _scope(tenant_id: str, owner_id: str | None) -> tuple[list[str], list]:
    clauses, params = [_RUN], [tenant_id]
    if owner_id is not None:
        clauses.append("AND r.owner_id=%s")
        params.append(owner_id)
    return clauses, params


def runs(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None,
         mode: str | None, gate: bool | None, dataset_id: str | None = None,
         profile_id: str | None = None, before: tuple | None, limit: int) -> list[dict]:
    clauses, params = _scope(tenant_id, owner_id)
    if eval_kind is not None:
        clauses.append("AND r.eval_kind=%s")
        params.append(eval_kind)
    if mode is not None:
        clauses.append("AND r.mode=%s")
        params.append(mode)
    if dataset_id is not None:
        clauses.append("AND r.dataset_id=%s")
        params.append(dataset_id)
    if profile_id is not None:
        clauses.append("AND r.profile_id=%s")
        params.append(profile_id)
    if gate is not None:
        clauses.append("AND r.gate_passed=%s")
        params.append(gate)
    if before is not None:
        clauses.append("AND (r.created_at,r.run_id)<(%s,%s)")
        params.extend(before)
    clauses.append("ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def datasets(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None) -> list[dict]:
    """按数据集汇总批次数量、最近批次与通过率，供评测中心展示与筛选。"""
    join_filter = "r.tenant_id=%s AND r.dataset_id=d.dataset_id"
    params: list[Any] = [tenant_id]
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    where = ""
    if eval_kind is not None:
        where = "WHERE d.eval_kind=%s"
        params.append(eval_kind)
    return fetch_all(conn, f"""
        SELECT d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.case_count,
               count(r.run_id) AS batch_count, max(r.created_at) AS last_run_at,
               avg(r.pass_rate) AS avg_pass_rate, max(r.pass_rate) AS best_pass_rate,
               COALESCE(sum(CASE WHEN r.gate_passed THEN 1 ELSE 0 END), 0) AS passed_batches
        FROM eval_datasets d
        LEFT JOIN eval_runs r ON {join_filter}
        {where}
        GROUP BY d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.case_count
        ORDER BY max(r.created_at) DESC NULLS LAST, d.version DESC, d.dataset_id
    """, tuple(params))


def dataset_detail(conn: Any, tenant_id: str, owner_id: str | None, dataset_id: str) -> dict | None:
    """读取指定数据集的完整元数据与用例内容清单。"""
    join_filter = "r.tenant_id=%s AND r.dataset_id=d.dataset_id"
    params: list[Any] = [tenant_id]
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    params.append(dataset_id)
    return fetch_one(conn, f"""
        SELECT d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.case_count,
               d.cases_payload, count(r.run_id) AS batch_count, max(r.created_at) AS last_run_at,
               avg(r.pass_rate) AS avg_pass_rate, max(r.pass_rate) AS best_pass_rate,
               COALESCE(sum(CASE WHEN r.gate_passed THEN 1 ELSE 0 END), 0) AS passed_batches
        FROM eval_datasets d
        LEFT JOIN eval_runs r ON {join_filter}
        WHERE d.dataset_id=%s
        GROUP BY d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.case_count, d.cases_payload
    """, tuple(params))


def profiles(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None) -> list[dict]:
    """列出不可变技术栈与 Prompt 版本及关联批次的聚合实测指标。"""
    join_filter = "r.tenant_id=p.tenant_id AND r.profile_id=p.profile_id"
    params: list[Any] = []
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    params.append(tenant_id)
    kind_clause = ""
    if eval_kind is not None:
        kind_clause = "AND p.eval_kind=%s"
        params.append(eval_kind)
    return fetch_all(conn, f"""
        SELECT p.profile_id, p.eval_kind, p.version_tag, p.tech_stack, p.retrieval_params,
               p.prompt_snapshots, p.prompt_sha256, p.notes, p.created_by, p.created_at,
               count(r.run_id) AS batch_count, avg(r.pass_rate) AS avg_pass_rate,
               avg(r.mean_score) AS avg_mean_score, avg(r.latency_p50_ms) AS avg_latency_p50_ms,
               avg(r.total_tokens) AS avg_tokens
        FROM eval_profiles p
        LEFT JOIN eval_runs r ON {join_filter}
        WHERE p.tenant_id=%s {kind_clause}
        GROUP BY p.profile_id, p.eval_kind, p.version_tag, p.tech_stack, p.retrieval_params,
                 p.prompt_snapshots, p.prompt_sha256, p.notes, p.created_by, p.created_at
        ORDER BY p.created_at DESC
    """, tuple(params))


def profile_by_id(conn: Any, tenant_id: str, profile_id: str) -> dict | None:
    """按主键查询指定租户下的配置版本。"""
    return fetch_one(conn, "SELECT * FROM eval_profiles WHERE tenant_id=%s AND profile_id=%s", (tenant_id, profile_id))


def insert_audit_log(
    conn: Any,
    tenant_id: str,
    actor_id: str,
    action: str,
    target_id: str,
    summary: dict[str, Any],
    *,
    git_sha: str | None = None,
) -> str:
    """记录受控写入操作的审计留痕。"""
    audit_id = f"aud_{uuid4().hex[:16]}"
    conn.execute("""
        INSERT INTO eval_audit_logs(audit_id, tenant_id, actor_id, action, target_id, git_sha, summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (audit_id, tenant_id, actor_id, action, target_id, git_sha, Jsonb(summary)))
    return audit_id


def insert_profile(conn: Any, tenant_id: str, actor_id: str, payload: dict[str, Any]) -> dict:
    """登记不可变技术栈与 Prompt 版本（Append-only，版本标签重复则拒绝）。"""
    canonical = json.dumps(payload.get("prompt_snapshots") or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    prompt_sha256 = sha256(canonical).hexdigest()
    profile_id = f"prof_{payload['eval_kind']}_{uuid4().hex[:12]}"
    inserted = conn.execute("""
        INSERT INTO eval_profiles(
            profile_id, tenant_id, eval_kind, version_tag, tech_stack,
            retrieval_params, prompt_snapshots, prompt_sha256, notes, created_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, eval_kind, version_tag) DO NOTHING
        RETURNING profile_id, eval_kind, version_tag, tech_stack, retrieval_params,
                  prompt_snapshots, prompt_sha256, notes, created_by, created_at
    """, (
        profile_id,
        tenant_id,
        payload["eval_kind"],
        payload["version_tag"],
        Jsonb(payload.get("tech_stack") or {}),
        Jsonb(payload.get("retrieval_params") or {}),
        Jsonb(payload.get("prompt_snapshots") or {}),
        prompt_sha256,
        payload.get("notes") or "",
        actor_id,
    )).fetchone()
    if inserted is None:
        raise ValueError("该评测类型下已存在同名版本标签，历史版本不可覆盖")
    insert_audit_log(
        conn,
        tenant_id,
        actor_id,
        "register_profile",
        profile_id,
        {"eval_kind": payload["eval_kind"], "version_tag": payload["version_tag"], "prompt_sha256": prompt_sha256},
    )
    return {
        **inserted,
        "batch_count": 0,
        "avg_pass_rate": None,
        "avg_mean_score": None,
        "avg_latency_p50_ms": None,
        "avg_tokens": None,
    }


def run(conn: Any, tenant_id: str, owner_id: str | None, *, run_id: str | None = None,
        report_digest: str | None = None) -> dict | None:
    clauses, params = _scope(tenant_id, owner_id)
    if run_id is not None:
        clauses.append("AND r.run_id=%s")
        params.append(run_id)
    else:
        clauses.append("AND r.report_sha256=%s")
        params.append(report_digest)
    return fetch_one(conn, " ".join(clauses), tuple(params))


def cases(conn: Any, tenant_id: str, run_id: str, *, status: str | None, after: str | None, limit: int) -> list[dict]:
    clauses = [f"SELECT {_CASE} FROM eval_case_results WHERE tenant_id=%s AND run_id=%s"]
    params = [tenant_id, run_id]
    if status is not None:
        clauses.append("AND status=%s")
        params.append(status)
    if after is not None:
        clauses.append("AND case_id>%s")
        params.append(after)
    clauses.append("ORDER BY case_id LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def case_detail(conn: Any, tenant_id: str, run_id: str, case_id: str) -> dict | None:
    return fetch_one(conn, f"SELECT {_CASE},expected,observed,steps_complete,verdicts,trace_id FROM eval_case_results "
                     "WHERE tenant_id=%s AND run_id=%s AND case_id=%s", (tenant_id, run_id, case_id))


def comparison_rows(conn: Any, tenant_id: str, run_id: str) -> list[dict]:
    return fetch_all(conn, "SELECT case_id,case_sha256,suite,status FROM eval_case_results "
                     "WHERE tenant_id=%s AND run_id=%s ORDER BY case_id", (tenant_id, run_id))


def trace_link(conn: Any, tenant_id: str, owner_id: str | None, trace_id: str) -> dict | None:
    sql = ("SELECT DISTINCT j.id AS task_id, s.trace_id FROM task_spans s "
           "JOIN jobs j ON (j.tenant_id,j.id)=(s.tenant_id,s.job_id) "
           "WHERE s.tenant_id=%s AND s.trace_id=%s AND j.kind IN ('ask','research')")
    params = [tenant_id, trace_id]
    if owner_id is not None:
        sql += " AND j.owner_id=%s"
        params.append(owner_id)
    rows = fetch_all(conn, sql + " LIMIT 2", tuple(params))
    return rows[0] if len(rows) == 1 else None


def insert_report(conn: Any, tenant_id: str, owner_id: str, report: dict, rows: list[dict]) -> bool:
    """在调用者的事务中原子导入，拒绝覆盖同名批次或更换其归属。"""
    if not fetch_one(conn, "SELECT id FROM users WHERE tenant_id=%s AND id=%s", (tenant_id, owner_id)):
        raise ValueError("评测发起人不属于指定租户")
    eval_kind = report.get("eval_kind", "agent")
    conn.execute("""
        INSERT INTO eval_datasets(dataset_id,name,eval_kind,version,description,source,case_count,schema_version,cases_payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dataset_id) DO UPDATE SET case_count=GREATEST(eval_datasets.case_count, EXCLUDED.case_count),
                                            cases_payload=CASE WHEN jsonb_array_length(eval_datasets.cases_payload)=0
                                                               THEN EXCLUDED.cases_payload ELSE eval_datasets.cases_payload END,
                                            updated_at=now()
    """, (
        report["dataset_id"],
        report["dataset_name"],
        eval_kind,
        report.get("dataset_version", "v1"),
        report.get("dataset_description", ""),
        report["mode"],
        report["passed_cases"] + report["failed_cases"] + report["unassessable_cases"],
        report["schema_version"],
        Jsonb(report.get("cases_payload") or []),
    ))
    target = {
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        **report,
        "eval_kind": eval_kind,
        "profile_id": report.get("profile_id"),
        "metrics": Jsonb(report["metrics"]),
        "judges": Jsonb(report["judges"]),
    }
    inserted = conn.execute("""
        INSERT INTO eval_runs(tenant_id,run_id,owner_id,created_at,mode,eval_kind,profile_id,git_sha,dataset_sha256,judges_sha256,
                              report_sha256,baseline_sha256,metrics,gate_passed,passed_cases,failed_cases,unassessable_cases,
                              dataset_id,pass_rate,mean_score,latency_p50_ms,latency_p95_ms,total_tokens,judges)
        VALUES (%(tenant_id)s,%(run_id)s,%(owner_id)s,%(created_at)s,%(mode)s,%(eval_kind)s,%(profile_id)s,%(git_sha)s,%(dataset_sha256)s,
                %(judges_sha256)s,%(report_sha256)s,%(baseline_sha256)s,%(metrics)s,%(gate_passed)s,%(passed_cases)s,
                %(failed_cases)s,%(unassessable_cases)s,%(dataset_id)s,%(pass_rate)s,%(mean_score)s,%(latency_p50_ms)s,
                %(latency_p95_ms)s,%(total_tokens)s,%(judges)s)
        ON CONFLICT (tenant_id,run_id) DO NOTHING RETURNING run_id
    """, target).fetchone()
    if inserted is None:
        existing = run(conn, tenant_id, owner_id, run_id=report["run_id"])
        if existing is None or existing["report_sha256"] != report["report_sha256"]:
            raise ValueError("同一批次编号已经归属于其他发起人或对应不同内容")
        conn.execute("""
            UPDATE eval_runs SET dataset_id=COALESCE(dataset_id,%s), pass_rate=COALESCE(pass_rate,%s),
                   mean_score=COALESCE(mean_score,%s), latency_p50_ms=COALESCE(latency_p50_ms,%s),
                   latency_p95_ms=COALESCE(latency_p95_ms,%s), total_tokens=COALESCE(total_tokens,%s),
                   judges=COALESCE(judges,%s)
            WHERE tenant_id=%s AND run_id=%s
        """, (report["dataset_id"], report["pass_rate"], report["mean_score"], report["latency_p50_ms"],
              report["latency_p95_ms"], report["total_tokens"], Jsonb(report["judges"]),
              tenant_id, report["run_id"]))
        return False
    with conn.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO eval_case_results(tenant_id,run_id,case_id,case_sha256,name,category,suite,status,score,
                                          hard_failure,trace_id,expected,observed,steps_complete,verdicts)
            VALUES (%(tenant_id)s,%(run_id)s,%(case_id)s,%(case_sha256)s,%(name)s,%(category)s,%(suite)s,%(status)s,
                    %(score)s,%(hard_failure)s,%(trace_id)s,%(expected)s,%(observed)s,%(steps_complete)s,%(verdicts)s)
        """, ({
            **row, "tenant_id": tenant_id, "run_id": report["run_id"],
            "expected": Jsonb(row["expected"]),
            "observed": Jsonb(row["observed"]) if row["observed"] is not None else None,
            "verdicts": Jsonb(row["verdicts"]),
        } for row in rows))
    return True
