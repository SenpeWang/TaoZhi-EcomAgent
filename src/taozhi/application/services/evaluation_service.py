"""评测中心：数据集版本、算法版本、基线、原始报告、指标趋势与评测任务。"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from taozhi.application.common.pagination import decode_time_cursor
from taozhi.domain.access import visible_owner_id
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.evaluation_comparison import pair_case_results
from taozhi.domain.roles import ROLE_LEVEL_BOSS
from taozhi.infrastructure.knowledge.storage import get_blob_store
from taozhi.infrastructure.persistence import evaluation_repo, job_repo
from taozhi.infrastructure.persistence.database import database_connection

REPORT_MAX_BYTES = 20 * 1024 * 1024
_DATASET_CASE_PAGE = 50


def _require_eval_writer(p: Principal) -> None:
    """评测写入（登记配置、设基线、触发执行）只允许老板或系统管理员。"""
    if p.role_level < ROLE_LEVEL_BOSS:
        raise Denied("仅系统管理员或企业负责人可执行评测写入操作", "FORBIDDEN", 403)


def _require_run(conn, p: Principal, run_id: str) -> dict:
    row = evaluation_repo.run(conn, p.tenant_id, visible_owner_id(p), run_id=run_id)
    if row is None:
        raise Denied("评测批次不存在", "NOT_FOUND", 404)
    return _decorate(row)


def _decorate(run: dict) -> dict:
    """把可比性标记换算成布尔值：历史版本缺失数据集摘要时显式标不可比。"""
    comparable_raw = run.pop("comparable_raw", None)
    run["comparable"] = comparable_raw != "false" and not run.get("dataset_archived")
    return run


def _compare(conn, tenant_id: str, current: dict, baseline: dict | None) -> dict | None:
    """逐例配对比较；口径、执行方式或判定器版本不同则拒绝给出对比结论。"""
    if baseline is None:
        return None
    if (current["mode"], current["judges_sha256"]) != (baseline["mode"], baseline["judges_sha256"]):
        return None
    if current.get("scope") != baseline.get("scope"):
        return None
    return {
        "baseline_sha256": baseline["report_sha256"],
        **pair_case_results(evaluation_repo.comparison_rows(conn, tenant_id, current["run_id"]),
                            evaluation_repo.comparison_rows(conn, tenant_id, baseline["run_id"])),
    }


def list_runs(p: Principal, *, owner_id: str | None, eval_kind: str | None, mode: str | None,
              gate_passed: bool | None, dataset_id: str | None, profile_id: str | None,
              scope: str | None, algorithm_version: str | None, baseline_only: bool,
              cursor: str | None, limit: int) -> dict:
    """按可见范围筛选评测批次，返回游标分页。"""
    owner = visible_owner_id(p, owner_id)
    before = decode_time_cursor(cursor)
    with database_connection() as conn:
        rows = evaluation_repo.runs(conn, p.tenant_id, owner, eval_kind=eval_kind, mode=mode, gate=gate_passed,
                                    dataset_id=dataset_id, profile_id=profile_id, scope=scope,
                                    algorithm_version=algorithm_version, baseline_only=baseline_only,
                                    before=before, limit=limit + 1)
    items = [_decorate(row) for row in rows[:limit]]
    following = f"{items[-1]['created_at'].isoformat()}|{items[-1]['run_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": following}


def list_datasets(p: Principal, *, eval_kind: str | None = None, version: str | None = None,
                  include_archived: bool = False) -> dict:
    """汇总当前账号可见范围内的数据集版本与批次指标。"""
    with database_connection() as conn:
        rows = evaluation_repo.datasets(conn, p.tenant_id, visible_owner_id(p), eval_kind=eval_kind,
                                        version=version, include_archived=include_archived)
    return {"items": rows}


def get_dataset(p: Principal, dataset_id: str, *, cursor: str | None, limit: int,
                suite: str | None = None) -> dict:
    """读取数据集版本详情与用例清单，用例按 offset 游标分页。"""
    with database_connection() as conn:
        row = evaluation_repo.dataset_detail(conn, p.tenant_id, visible_owner_id(p), dataset_id)
    if row is None:
        raise Denied("评测数据集不存在", "NOT_FOUND", 404)
    payload = row.pop("cases_payload") or []
    if suite:
        payload = [case for case in payload if (case.get("suite") or "") == suite]
    offset = int(cursor) if cursor else 0
    page = payload[offset:offset + limit]
    following = str(offset + limit) if offset + limit < len(payload) else None
    return {"dataset": row, "cases": page, "next_cursor": following, "total_cases": len(payload)}


def list_profiles(p: Principal, *, eval_kind: str | None = None, dataset_version: str | None = None) -> dict:
    """列出算法版本（技术栈 + 检索参数 + Prompt 快照）与关联批次聚合指标。"""
    with database_connection() as conn:
        rows = evaluation_repo.profiles(conn, p.tenant_id, visible_owner_id(p), eval_kind=eval_kind,
                                        dataset_version=dataset_version)
    return {"items": rows}


def create_profile(p: Principal, payload: dict) -> dict:
    """登记算法版本：同摘要自动复用已有版本号，显式版本标签重复则冲突。"""
    _require_eval_writer(p)
    with database_connection() as conn:
        try:
            return evaluation_repo.insert_profile(conn, p.tenant_id, p.id, payload)
        except ValueError as exc:
            raise Denied(str(exc), "CONFLICT", 409) from exc


def list_baselines(p: Principal, *, eval_kind: str | None = None, dataset_version: str | None = None,
                   include_history: bool = True) -> dict:
    """列出 active 与历史基线（含原始基线），附带对应批次指标与算法版本。"""
    with database_connection() as conn:
        rows = evaluation_repo.baselines(conn, p.tenant_id, eval_kind=eval_kind,
                                         dataset_version=dataset_version, include_history=include_history)
    return {"items": rows}


def set_baseline(p: Principal, run_id: str, note: str) -> dict:
    """把指定批次设为该数据集版本 + 口径 + 方式的 active 基线，旧基线保留历史。"""
    _require_eval_writer(p)
    with database_connection() as conn:
        run = _require_run(conn, p, run_id)
        if not run["dataset_id"]:
            raise Denied("批次未关联数据集，无法设为基线", "INVALID_BASELINE", 422)
        return evaluation_repo.set_baseline(conn, p.tenant_id, p.id, run=run, note=note)


def get_run(p: Principal, run_id: str) -> dict:
    """读取批次详情：主指标、原始报告可用性与报告自带基线对比。"""
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        digest = current["baseline_sha256"]
        baseline = evaluation_repo.run(conn, p.tenant_id, visible_owner_id(p), report_digest=digest) if digest else None
        comparison = _compare(conn, p.tenant_id, current, _decorate(baseline) if baseline else None)
    return {"run": current, "comparison": comparison, "metrics": current["metrics"]}


def _report_key(run: dict) -> str:
    """原始报告在对象存储中的键：按租户、评测类型、数据集与批次隔离。"""
    return (f"evaluations/{run['eval_kind']}/{run.get('dataset_id') or 'unknown'}/"
            f"{run['run_id']}/report.json")


def store_report(conn, tenant_id: str, run_id: str, *, eval_kind: str, dataset_id: str | None,
                 payload: bytes) -> bool:
    """把原始报告写入对象存储并登记校验摘要；重复写入内容不一致时拒绝。"""
    if len(payload) > REPORT_MAX_BYTES:
        raise ValueError("原始报告超过单份大小上限，拒绝入库")
    key = (f"evaluations/{eval_kind}/{dataset_id or 'unknown'}/{run_id}/report.json")
    digest = sha256(payload).hexdigest()
    get_blob_store().put_blob(tenant_id, key, payload)
    return evaluation_repo.insert_run_report(conn, tenant_id, run_id, object_key=key, digest=digest,
                                             byte_size=len(payload))


def get_run_report(p: Principal, run_id: str) -> dict:
    """读取批次原始报告（JSON 字节），按登记摘要校验后返回。"""
    with database_connection() as conn:
        _require_run(conn, p, run_id)
        recorded = evaluation_repo.run_report(conn, p.tenant_id, run_id)
    if recorded is None:
        raise Denied("该批次未保存原始报告", "NOT_FOUND", 404)
    payload = get_blob_store().open_blob(p.tenant_id, recorded["object_key"])
    if sha256(payload).hexdigest() != recorded["sha256"]:
        raise Denied("原始报告校验失败，可能已被篡改", "CORRUPT_REPORT", 409)
    return {"run_id": run_id, "payload": payload, "sha256": recorded["sha256"],
            "byte_size": recorded["byte_size"], "content_type": recorded["content_type"]}


def trends(p: Principal, *, eval_kind: str | None, dataset_version: str | None, scope: str | None,
           mode: str | None, limit: int) -> dict:
    """返回批次主指标时间序列、active 基线摘要与相对基线的差值。"""
    with database_connection() as conn:
        rows = [_decorate(row) for row in evaluation_repo.trend_runs(
            conn, p.tenant_id, visible_owner_id(p), eval_kind=eval_kind,
            dataset_version=dataset_version, scope=scope, mode=mode, limit=limit)]
        latest = rows[-1] if rows else None
        resolved_version = dataset_version or (latest or {}).get("dataset_version")
        resolved_scope = scope or (latest or {}).get("scope")
        resolved_mode = mode or (latest or {}).get("mode")
        baseline = None
        if eval_kind and resolved_version and resolved_scope and resolved_mode:
            baseline = evaluation_repo.active_baseline(
                conn, p.tenant_id, eval_kind=eval_kind, dataset_version=resolved_version,
                scope=resolved_scope, mode=resolved_mode)
    baseline_rate = baseline["pass_rate"] if baseline else None
    items = []
    for row in rows:
        delta = None
        if baseline_rate is not None and row["pass_rate"] is not None:
            delta = round(row["pass_rate"] - baseline_rate, 4)
        items.append({
            "run_id": row["run_id"], "created_at": row["created_at"], "mode": row["mode"],
            "scope": row["scope"], "dataset_version": row["dataset_version"],
            "algorithm_version": row["profile_version_tag"], "gate_passed": row["gate_passed"],
            "pass_rate": row["pass_rate"], "mean_score": row["mean_score"],
            "latency_p50_ms": row["latency_p50_ms"], "latency_p95_ms": row["latency_p95_ms"],
            "total_tokens": row["total_tokens"], "is_baseline": row["is_baseline"],
            "comparable": row["comparable"], "pass_rate_delta": delta,
        })
    rates = [row["pass_rate"] for row in rows if row["pass_rate"] is not None]
    return {
        "dataset_version": resolved_version, "scope": resolved_scope, "mode": resolved_mode,
        "items": items,
        "aggregate": {
            "batches": len(items),
            "avg_pass_rate": round(sum(rates) / len(rates), 4) if rates else None,
            "best_pass_rate": max(rates) if rates else None,
            "latest_pass_rate": rates[-1] if rates else None,
        },
        "baseline": None if baseline is None else {
            "baseline_id": baseline["baseline_id"], "run_id": baseline["run_id"], "note": baseline["note"],
            "created_at": baseline["baseline_created_at"], "pass_rate": baseline["pass_rate"],
            "gate_passed": baseline["gate_passed"], "report_sha256": baseline["report_sha256"],
        },
    }


def layers(p: Principal, run_id: str) -> dict:
    """返回 RAG 分层指标（检索 / 对抗 / 行为 / 六项工程指标）；Agent 批次返回空集。"""
    with database_connection() as conn:
        run = _require_run(conn, p, run_id)
    metrics = run["metrics"] or {}
    names = ("retrieval", "adversarial", "behavior", "six_metrics")
    return {"run_id": run_id, "eval_kind": run["eval_kind"],
            "layers": {name: metrics.get(name) for name in names if metrics.get(name) is not None}}


def compare(p: Principal, run_id: str, baseline_id: str) -> dict:
    """按逐例配对比较两个批次，口径或判定器不一致时明确拒绝。"""
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        baseline = _require_run(conn, p, baseline_id)
        if run_id == baseline_id:
            raise Denied("请选择另一个评测批次作为基线", "INVALID_BASELINE", 422)
        comparison = _compare(conn, p.tenant_id, current, baseline)
        if comparison is None:
            raise Denied("数据集版本、口径、执行方式和判定器版本必须一致", "INCOMPATIBLE_BASELINE", 422)
        return comparison


def list_cases(p: Principal, run_id: str, *, status: str | None, suite: str | None,
               cursor: str | None, limit: int) -> dict:
    """按状态与用例分组读取批次逐例判定。"""
    with database_connection() as conn:
        _require_run(conn, p, run_id)
        rows = evaluation_repo.cases(conn, p.tenant_id, run_id, status=status, suite=suite,
                                     after=cursor, limit=limit + 1)
    items = rows[:limit]
    return {"items": items, "next_cursor": items[-1]["case_id"] if len(rows) > limit else None}


def get_case(p: Principal, run_id: str, case_id: str) -> dict:
    with database_connection() as conn:
        current = _require_run(conn, p, run_id)
        row = evaluation_repo.case_detail(conn, p.tenant_id, run_id, case_id)
        if row is None:
            raise Denied("评测用例不存在", "NOT_FOUND", 404)
        trace_id = row.pop("trace_id")
        link = (evaluation_repo.trace_link(conn, p.tenant_id, visible_owner_id(p), trace_id)
                if trace_id and current["mode"] == "live" else None)
    return {"expected": row.pop("expected"), "observed": row.pop("observed"),
            "verdicts": row.pop("verdicts"), "steps_complete": row.pop("steps_complete"),
            "case": row, "trace_link": link}


def list_audit_logs(p: Principal, *, action: str | None, cursor: str | None, limit: int) -> dict:
    """读取评测写入审计留痕（登记数据集/配置、触发、设基线）。"""
    _require_eval_writer(p)
    before = decode_time_cursor(cursor)
    with database_connection() as conn:
        rows = evaluation_repo.audit_logs(conn, p.tenant_id, action=action, before=before, limit=limit + 1)
    items = rows[:limit]
    following = f"{items[-1]['created_at'].isoformat()}|{items[-1]['audit_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": following}


def trigger(p: Principal, payload: dict) -> dict:
    """校验数据集版本与算法版本后创建 evaluation 任务，由 Worker 真实执行。"""
    _require_eval_writer(p)
    with database_connection() as conn:
        dataset = evaluation_repo.dataset_row(conn, payload["dataset_id"])
        if dataset is None or dataset["archived"]:
            raise Denied("所选评测数据集不存在或已归档", "NOT_FOUND", 404)
        if dataset["eval_kind"] != payload["eval_kind"]:
            raise Denied("评测类型与数据集不一致", "INVALID_EVAL_KIND", 422)
        metadata = dataset.get("metadata") or {}
        allowed_scopes = metadata.get("scopes") or ["full"]
        if payload["scope"] not in allowed_scopes:
            raise Denied(f"该数据集支持的口径为 {'、'.join(allowed_scopes)}", "INVALID_SCOPE", 422)
        allowed_modes = metadata.get("modes") or ["live"]
        if payload["mode"] not in allowed_modes:
            raise Denied(f"该数据集支持的执行方式为 {'、'.join(allowed_modes)}", "INVALID_MODE", 422)
        if payload.get("profile_id"):
            profile = evaluation_repo.profile_by_id(conn, p.tenant_id, payload["profile_id"])
            if profile is None or profile["eval_kind"] != payload["eval_kind"]:
                raise Denied("所选算法版本不存在或类型不匹配", "NOT_FOUND", 404)
        if job_repo.has_active_kind(conn, p.tenant_id, "evaluation"):
            raise Denied("已有评测任务在排队或执行，请稍后再发起", "CONFLICT", 409)
        job_payload = {
            "eval_kind": payload["eval_kind"], "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["version"], "scope": payload["scope"], "mode": payload["mode"],
            "profile_id": payload.get("profile_id") or "", "note": payload.get("note") or "",
            "mark_baseline": bool(payload.get("mark_baseline")),
        }
        job = job_repo.create_job(conn, p, "evaluation", p.root, job_payload, question="", level=1)
        audit_id = evaluation_repo.insert_audit_log(
            conn, p.tenant_id, p.id, "trigger_run", job["id"],
            {"eval_kind": payload["eval_kind"], "dataset_id": dataset["dataset_id"],
             "dataset_version": dataset["version"], "scope": payload["scope"], "mode": payload["mode"],
             "profile_id": payload.get("profile_id") or "", "note": payload.get("note") or ""},
        )
    return {"job_id": job["id"], "audit_id": audit_id, "status": job["state"]}


def _require_job(p: Principal, conn, job_id: str) -> dict:
    job = job_repo.get_job(conn, p.tenant_id, job_id)
    if job is None or job["kind"] != "evaluation":
        raise Denied("评测任务不存在", "NOT_FOUND", 404)
    if job["owner_id"] != p.id and p.role_level < ROLE_LEVEL_BOSS:
        raise Denied()
    return job


def job_status(p: Principal, job_id: str) -> dict:
    """读取评测任务状态、阶段进度与终态结果。"""
    with database_connection() as conn:
        job = _require_job(p, conn, job_id)
    return {"job_id": job["id"], "state": job["state"], "stage": job["stage"], "progress": job["progress"],
            "result": job["result"], "error": job["error"], "created_at": job["created_at"],
            "started_at": job["started_at"], "finished_at": job["finished_at"]}


def cancel_job(p: Principal, job_id: str) -> dict:
    """请求取消排队或执行中的评测任务；执行中由 Worker 终止评测子进程。"""
    with database_connection() as conn:
        job = _require_job(p, conn, job_id)
        if job["state"] not in ("queued", "running"):
            raise Denied("评测任务已经结束", "CONFLICT", 409)
        job_repo.mark_job_cancelled(conn, job_id)
    return {"message": "已请求取消"}