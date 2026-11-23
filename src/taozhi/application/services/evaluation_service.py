"""在已授权的发起人范围内查看评测运行、失败证据和基线差异。"""
from __future__ import annotations

from taozhi.application.common.pagination import decode_time_cursor
from taozhi.domain.access import visible_owner_id
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.evaluation_comparison import pair_case_results
from taozhi.domain.roles import ROLE_LEVEL_BOSS
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


def _require_eval_writer(p: Principal) -> None:
    if p.role_level < ROLE_LEVEL_BOSS:
        raise Denied("仅系统管理员或企业负责人可执行评测写入操作", "FORBIDDEN", 403)


def list_runs(p: Principal, *, owner_id: str | None, eval_kind: str | None, mode: str | None,
              gate_passed: bool | None, dataset_id: str | None, profile_id: str | None,
              cursor: str | None, limit: int) -> dict:
    owner = visible_owner_id(p, owner_id)
    before = decode_time_cursor(cursor)
    with database_connection() as conn:
        rows = evaluation_repo.runs(conn, p.tenant_id, owner, eval_kind=eval_kind, mode=mode, gate=gate_passed,
                                    dataset_id=dataset_id, profile_id=profile_id, before=before, limit=limit + 1)
    items = rows[:limit]
    following = f"{items[-1]['created_at'].isoformat()}|{items[-1]['run_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": following}


def list_datasets(p: Principal, *, eval_kind: str | None = None) -> dict:
    """汇总当前账号可见范围内的数据集与批次指标。"""
    with database_connection() as conn:
        rows = evaluation_repo.datasets(conn, p.tenant_id, visible_owner_id(p), eval_kind=eval_kind)
    return {"items": rows}


def get_dataset(p: Principal, dataset_id: str) -> dict:
    """读取指定数据集详情及用例清单。"""
    with database_connection() as conn:
        row = evaluation_repo.dataset_detail(conn, p.tenant_id, visible_owner_id(p), dataset_id)
    if row is None:
        raise Denied("评测数据集不存在", "NOT_FOUND", 404)
    cases_payload = row.pop("cases_payload") or []
    return {"dataset": row, "cases": cases_payload}


def list_profiles(p: Principal, *, eval_kind: str | None = None) -> dict:
    """列出各技术栈/Prompt 不可变版本及其聚合指标对比。"""
    with database_connection() as conn:
        rows = evaluation_repo.profiles(conn, p.tenant_id, visible_owner_id(p), eval_kind=eval_kind)
    return {"items": rows}


def create_profile(p: Principal, payload: dict) -> dict:
    """登记新的不可变技术栈与 Prompt 调优版本（窄写受控且审计留痕）。"""
    _require_eval_writer(p)
    with database_connection() as conn:
        try:
            return evaluation_repo.insert_profile(conn, p.tenant_id, p.id, payload)
        except ValueError as exc:
            raise Denied(str(exc), "CONFLICT", 409) from exc


def trigger_evaluation(p: Principal, payload: dict) -> dict:
    """校验数据集与配置版本后发起受控评测请求并记录审计留痕。"""
    _require_eval_writer(p)
    with database_connection() as conn:
        dataset = evaluation_repo.dataset_detail(conn, p.tenant_id, None, payload["dataset_id"])
        if dataset is None or dataset["eval_kind"] != payload["eval_kind"]:
            raise Denied("所选评测数据集不存在或类型不匹配", "NOT_FOUND", 404)
        profile = evaluation_repo.profile_by_id(conn, p.tenant_id, payload["profile_id"])
        if profile is None or profile["eval_kind"] != payload["eval_kind"]:
            raise Denied("所选技术栈/Prompt 配置版本不存在或类型不匹配", "NOT_FOUND", 404)
        audit_id = evaluation_repo.insert_audit_log(
            conn,
            p.tenant_id,
            p.id,
            "trigger_run",
            payload["dataset_id"],
            {
                "eval_kind": payload["eval_kind"],
                "dataset_id": payload["dataset_id"],
                "dataset_version": dataset["version"],
                "profile_id": payload["profile_id"],
                "version_tag": profile["version_tag"],
                "note": payload.get("note") or "",
            },
        )
    return {"audit_id": audit_id, "run_id": None, "status": "recorded"}


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
