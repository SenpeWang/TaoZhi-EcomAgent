"""在已授权的发起人范围内查看评测运行、失败证据和基线差异。"""
from __future__ import annotations

import re
from datetime import datetime

from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.evaluation_comparison import pair_case_results
from taozhi.domain.roles import is_system_admin
from taozhi.infrastructure.persistence import evaluation_repo as repo
from taozhi.knowledge.db import database_connection


def _owner(p: Principal, requested: str | None = None) -> str | None:
    if is_system_admin(p.role_level):
        return None if requested in (None, "all") else requested
    if requested not in (None, p.id):
        raise Denied("只能查看本人发起的评测")
    return p.id


def _require_run(conn, p: Principal, run_id: str) -> dict:
    row = repo.run(conn, p.tenant_id, _owner(p), run_id=run_id)
    if row is None:
        raise Denied("评测批次不存在", "NOT_FOUND", 404)
    return row


def _compare(conn, tenant_id: str, current: dict, baseline: dict) -> dict:
    if (current["mode"], current["judges_sha256"]) != (baseline["mode"], baseline["judges_sha256"]):
        raise Denied("执行方式和判定器版本必须一致", "INCOMPATIBLE_BASELINE", 422)
    return {
        "baseline_sha256": baseline["report_sha256"],
        **pair_case_results(repo.comparison_rows(conn, tenant_id, current["run_id"]),
                            repo.comparison_rows(conn, tenant_id, baseline["run_id"])),
    }


def list_runs(p: Principal, *, owner_id: str | None, mode: str | None, gate_passed: bool | None,
              cursor: str | None, limit: int) -> dict:
    owner = _owner(p, owner_id)
    before = None
    if cursor is not None:
        try:
            timestamp, run_id = cursor.split("|", 1)
            created = datetime.fromisoformat(timestamp)
            if created.tzinfo is None or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_id):
                raise ValueError
            before = (created, run_id)
        except ValueError as exc:
            raise Denied("评测分页游标无效", "INVALID_CURSOR", 422) from exc
    with database_connection() as conn:
        rows = repo.runs(conn, p.tenant_id, owner, mode=mode, gate=gate_passed, before=before, limit=limit + 1)
    items = rows[:limit]
    following = f"{items[-1]['created_at'].isoformat()}|{items[-1]['run_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": following}


def get_run(p: Principal, run_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        digest = current["baseline_sha256"]
        baseline = repo.run(conn, p.tenant_id, _owner(p), report_digest=digest) if digest else None
        comparison = _compare(conn, p.tenant_id, current, baseline) if baseline else None
    return {"run": current, "comparison": comparison, "metrics": current["metrics"]}


def compare(p: Principal, run_id: str, baseline_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        baseline = _require_run(conn, p, baseline_id)
        if run_id == baseline_id:
            raise Denied("请选择另一个评测批次作为基线", "INVALID_BASELINE", 422)
        return _compare(conn, p.tenant_id, current, baseline)


def list_cases(p: Principal, run_id: str, *, status: str | None, cursor: str | None, limit: int) -> dict:
    with database_connection() as conn:
        _require_run(conn, p, run_id)
        rows = repo.cases(conn, p.tenant_id, run_id, status=status, after=cursor, limit=limit + 1)
    items = rows[:limit]
    return {"items": items, "next_cursor": items[-1]["case_id"] if len(rows) > limit else None}


def get_case(p: Principal, run_id: str, case_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        row = repo.case_detail(conn, p.tenant_id, run_id, case_id)
        if row is None:
            raise Denied("评测用例不存在", "NOT_FOUND", 404)
        trace_id = row.pop("trace_id")
        link = repo.trace_link(conn, p.tenant_id, _owner(p), trace_id) if trace_id and current["mode"] == "live" else None
    return {"expected": row.pop("expected"), "observed": row.pop("observed"),
            "verdicts": row.pop("verdicts"), "steps_complete": row.pop("steps_complete"), "case": row, "trace_link": link}
