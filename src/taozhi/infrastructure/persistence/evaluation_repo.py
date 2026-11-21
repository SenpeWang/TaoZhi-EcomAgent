"""租户范围内的评测批次与用例读写。"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from taozhi.knowledge.db import fetch_all, fetch_one


_RUN = """
    SELECT r.run_id, r.owner_id, u.display_name AS owner_name, r.created_at, r.mode, r.git_sha,
           r.dataset_sha256, r.judges_sha256, r.gate_passed, r.passed_cases, r.failed_cases, r.unassessable_cases,
           r.passed_cases+r.failed_cases+r.unassessable_cases AS total_cases, r.metrics, r.report_sha256, r.baseline_sha256
    FROM eval_runs r JOIN users u ON (u.tenant_id,u.id)=(r.tenant_id,r.owner_id)
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
         before: tuple | None, limit: int) -> list[dict]:
    clauses, params = _scope(tenant_id, owner_id)
    if mode is not None:
        clauses.append("AND r.mode=%s")
        params.append(mode)
    if gate is not None:
        clauses.append("AND r.gate_passed=%s")
        params.append(gate)
    if before is not None:
        clauses.append("AND (r.created_at,r.run_id)<(%s,%s)")
        params.extend(before)
    clauses.append("ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


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
    target = {"tenant_id": tenant_id, "owner_id": owner_id, **report, "metrics": Jsonb(report["metrics"])}
    inserted = conn.execute("""
        INSERT INTO eval_runs(tenant_id,run_id,owner_id,created_at,mode,git_sha,dataset_sha256,judges_sha256,
                              report_sha256,baseline_sha256,metrics,gate_passed,passed_cases,failed_cases,unassessable_cases)
        VALUES (%(tenant_id)s,%(run_id)s,%(owner_id)s,%(created_at)s,%(mode)s,%(git_sha)s,%(dataset_sha256)s,
                %(judges_sha256)s,%(report_sha256)s,%(baseline_sha256)s,%(metrics)s,%(gate_passed)s,%(passed_cases)s,
                %(failed_cases)s,%(unassessable_cases)s)
        ON CONFLICT (tenant_id,run_id) DO NOTHING RETURNING run_id
    """, target).fetchone()
    if inserted is None:
        existing = run(conn, tenant_id, owner_id, run_id=report["run_id"])
        if existing is None or existing["report_sha256"] != report["report_sha256"]:
            raise ValueError("同一批次编号已经归属于其他发起人或对应不同内容")
        return False
    with conn.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO eval_case_results(tenant_id,run_id,case_id,case_sha256,name,category,suite,status,score,
                                          hard_failure,trace_id,expected,observed,steps_complete,verdicts)
            VALUES (%(tenant_id)s,%(run_id)s,%(case_id)s,%(case_sha256)s,%(name)s,%(category)s,%(suite)s,%(status)s,
                    %(score)s,%(hard_failure)s,%(trace_id)s,%(expected)s,%(observed)s,%(steps_complete)s,%(verdicts)s)
        """, ({
            **row, "tenant_id": tenant_id, "run_id": report["run_id"],
            "expected": Jsonb(row["expected"]) if row["expected"] is not None else None,
            "observed": Jsonb(row["observed"]) if row["observed"] is not None else None,
            "verdicts": Jsonb(row["verdicts"]),
        } for row in rows))
    return True
