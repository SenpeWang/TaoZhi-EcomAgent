"""在已授权的发起人范围内查看评测运行、失败证据和基线差异。"""
from __future__ import annotations

from taozhi.application.common.pagination import decode_time_cursor
from taozhi.domain.access import visible_owner_id
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.evaluation_comparison import pair_case_results
from taozhi.infrastructure.persistence import evaluation_repo
from taozhi.infrastructure.persistence.database import database_connection


def _require_run(conn, p: Principal, run_id: str) -> dict:
    row = evaluation_repo.run(conn, p.tenant_id, visible_owner_id(p), run_id=run_id)
    if row is None:
        raise Denied("评测批次不存在", "NOT_FOUND", 404)
    return row


def _compare(conn, tenant_id: str, current: dict, baseline: dict | None) -> dict | None:
    if baseline is None or (current["mode"], current["judges_sha256"]) != (baseline["mode"], baseline["judges_sha256"]):
        return None
    return {
        "baseline_sha256": baseline["report_sha256"],
        **pair_case_results(evaluation_repo.comparison_rows(conn, tenant_id, current["run_id"]),
                            evaluation_repo.comparison_rows(conn, tenant_id, baseline["run_id"])),
    }


def list_runs(p: Principal, *, owner_id: str | None, mode: str | None, gate_passed: bool | None,
              dataset_id: str | None, cursor: str | None, limit: int) -> dict:
    owner = visible_owner_id(p, owner_id)
    before = decode_time_cursor(cursor)
    with database_connection() as conn:
        rows = evaluation_repo.runs(conn, p.tenant_id, owner, mode=mode, gate=gate_passed,
                                    dataset_id=dataset_id, before=before, limit=limit + 1)
    items = rows[:limit]
    following = f"{items[-1]['created_at'].isoformat()}|{items[-1]['run_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": following}


def list_datasets(p: Principal) -> dict:
    """汇总当前账号可见范围内的数据集与批次指标。"""
    with database_connection() as conn:
        rows = evaluation_repo.datasets(conn, p.tenant_id, visible_owner_id(p))
    return {"items": rows}


def get_run(p: Principal, run_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        digest = current["baseline_sha256"]
        baseline = evaluation_repo.run(conn, p.tenant_id, visible_owner_id(p), report_digest=digest) if digest else None
        comparison = _compare(conn, p.tenant_id, current, baseline)
    return {"run": current, "comparison": comparison, "metrics": current["metrics"]}


def compare(p: Principal, run_id: str, baseline_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        baseline = _require_run(conn, p, baseline_id)
        if run_id == baseline_id:
            raise Denied("请选择另一个评测批次作为基线", "INVALID_BASELINE", 422)
        comparison = _compare(conn, p.tenant_id, current, baseline)
        if comparison is None:
            raise Denied("执行方式和判定器版本必须一致", "INCOMPATIBLE_BASELINE", 422)
        return comparison


def list_cases(p: Principal, run_id: str, *, status: str | None, cursor: str | None, limit: int) -> dict:
    with database_connection() as conn:
        _require_run(conn, p, run_id)
        rows = evaluation_repo.cases(conn, p.tenant_id, run_id, status=status, after=cursor, limit=limit + 1)
    items = rows[:limit]
    return {"items": items, "next_cursor": items[-1]["case_id"] if len(rows) > limit else None}


def get_case(p: Principal, run_id: str, case_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        row = evaluation_repo.case_detail(conn, p.tenant_id, run_id, case_id)
        if row is None:
            raise Denied("评测用例不存在", "NOT_FOUND", 404)
        trace_id = row.pop("trace_id")
        link = evaluation_repo.trace_link(conn, p.tenant_id, visible_owner_id(p), trace_id) if trace_id and current["mode"] == "live" else None
    return {"expected": row.pop("expected"), "observed": row.pop("observed"),
            "verdicts": row.pop("verdicts"), "steps_complete": row.pop("steps_complete"), "case": row, "trace_link": link}
