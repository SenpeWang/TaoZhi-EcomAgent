"""租户范围内的评测批次与用例读写。"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from taozhi.infrastructure.persistence.database import fetch_all, fetch_one


_RUN = """
    SELECT r.run_id, r.owner_id, u.display_name AS owner_name, r.created_at, r.mode, r.git_sha,
           r.dataset_sha256, r.judges_sha256, r.gate_passed, r.passed_cases, r.failed_cases, r.unassessable_cases,
           r.metrics->'provenance'->>'transcript_sha256' AS transcript_sha256,
           r.metrics->'provenance'->>'counterexamples_sha256' AS counterexamples_sha256,
           r.metrics->'provenance'->>'model_config_sha256' AS model_config_sha256,
           r.passed_cases+r.failed_cases+r.unassessable_cases AS total_cases, r.metrics, r.report_sha256, r.baseline_sha256,
           r.dataset_id, d.name AS dataset_name, r.pass_rate, r.mean_score, r.latency_p50_ms, r.latency_p95_ms,
           r.total_tokens, r.judges
    FROM eval_runs r JOIN users u ON (u.tenant_id,u.id)=(r.tenant_id,r.owner_id)
    LEFT JOIN eval_datasets d ON d.dataset_id=r.dataset_id
    WHERE r.tenant_id=%s
"""
_CASE = "case_id,name,category,suite,status,score,hard_failure"


def _scope(tenant_id: str, owner_id: str | None) -> tuple[list[str], list]:
    clauses, params = [_RUN], [tenant_id]
    if owner_id is not None:
        clauses.append("AND r.owner_id=%s")
        params.append(owner_id)
    return clauses, params


def runs(conn: Any, tenant_id: str, owner_id: str | None, *, mode: str | None, gate: bool | None,
         dataset_id: str | None = None, before: tuple | None, limit: int) -> list[dict]:
    clauses, params = _scope(tenant_id, owner_id)
    if mode is not None:
        clauses.append("AND r.mode=%s")
        params.append(mode)
    if dataset_id is not None:
        clauses.append("AND r.dataset_id=%s")
        params.append(dataset_id)
    if gate is not None:
        clauses.append("AND r.gate_passed=%s")
        params.append(gate)
    if before is not None:
        clauses.append("AND (r.created_at,r.run_id)<(%s,%s)")
        params.extend(before)
    clauses.append("ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def datasets(conn: Any, tenant_id: str, owner_id: str | None) -> list[dict]:
    """按数据集汇总批次数量、最近批次与通过率，供评测中心展示入库水位。"""
    clauses = ["WHERE r.tenant_id=%s"]
    params = [tenant_id]
    if owner_id is not None:
        clauses.append("AND r.owner_id=%s")
        params.append(owner_id)
    return fetch_all(conn, f"""
        SELECT d.dataset_id, d.name, d.source, d.case_count,
               count(r.run_id) AS batch_count, max(r.created_at) AS last_run_at,
               avg(r.pass_rate) AS avg_pass_rate, max(r.pass_rate) AS best_pass_rate,
               sum(CASE WHEN r.gate_passed THEN 1 ELSE 0 END) AS passed_batches
        FROM eval_runs r JOIN eval_datasets d ON d.dataset_id=r.dataset_id
        {" ".join(clauses)}
        GROUP BY d.dataset_id,d.name,d.source,d.case_count
        ORDER BY max(r.created_at) DESC, d.dataset_id
    """, tuple(params))


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
    conn.execute("""
        INSERT INTO eval_datasets(dataset_id,name,source,case_count,schema_version)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (dataset_id) DO UPDATE SET case_count=GREATEST(eval_datasets.case_count, EXCLUDED.case_count),
                                            updated_at=now()
    """, (report["dataset_id"], report["dataset_name"], report["mode"],
          report["passed_cases"] + report["failed_cases"] + report["unassessable_cases"], report["schema_version"]))
    target = {"tenant_id": tenant_id, "owner_id": owner_id, **report, "metrics": Jsonb(report["metrics"]),
              "judges": Jsonb(report["judges"])}
    inserted = conn.execute("""
        INSERT INTO eval_runs(tenant_id,run_id,owner_id,created_at,mode,git_sha,dataset_sha256,judges_sha256,
                              report_sha256,baseline_sha256,metrics,gate_passed,passed_cases,failed_cases,unassessable_cases,
                              dataset_id,pass_rate,mean_score,latency_p50_ms,latency_p95_ms,total_tokens,judges)
        VALUES (%(tenant_id)s,%(run_id)s,%(owner_id)s,%(created_at)s,%(mode)s,%(git_sha)s,%(dataset_sha256)s,
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
