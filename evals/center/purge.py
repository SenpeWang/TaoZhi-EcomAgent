"""评测中心数据清理：清理历史版本与旧批次，默认仅预演，执行时留审计。

清理口径：
- legacy：删除已归档数据集及其全部批次、基线、原始报告对象（保留 V1 数据集与算法版本）；
- runs：删除全部批次、基线、算法版本与原始报告对象（保留数据集登记，便于重新跑基线）；
- all：在 runs 基础上删除数据集登记（跨租户资产，破坏性最强）。
"""
from __future__ import annotations

from typing import Any

from taozhi.infrastructure.knowledge.storage import get_blob_store
from taozhi.infrastructure.persistence import evaluation_repo
from taozhi.infrastructure.persistence.database import database_connection, fetch_all

MODES = ("legacy", "runs", "all")


def _target_scope(conn: Any, tenant_id: str, mode: str) -> tuple[list[str], list[str]]:
    """返回待清理的批次编号与数据集编号。"""
    if mode == "legacy":
        datasets = [row["dataset_id"] for row in fetch_all(
            conn, "SELECT dataset_id FROM eval_datasets WHERE archived ORDER BY dataset_id")]
        runs = [row["run_id"] for row in fetch_all(
            conn, "SELECT r.run_id FROM eval_runs r JOIN eval_datasets d ON d.dataset_id=r.dataset_id "
                  "WHERE r.tenant_id=%s AND d.archived ORDER BY r.run_id", (tenant_id,))]
        return runs, datasets
    runs = [row["run_id"] for row in fetch_all(
        conn, "SELECT run_id FROM eval_runs WHERE tenant_id=%s ORDER BY run_id", (tenant_id,))]
    datasets = [] if mode == "runs" else [row["dataset_id"] for row in fetch_all(
        conn, "SELECT dataset_id FROM eval_datasets ORDER BY dataset_id")]
    return runs, datasets


def purge(*, tenant_id: str, actor_id: str, mode: str, include_audit: bool = False,
          dry_run: bool = True) -> dict:
    """按口径清理评测中心数据；dry_run=True 时只统计不写入。"""
    if mode not in MODES:
        raise ValueError(f"清理口径仅支持 {'、'.join(MODES)}")
    with database_connection() as conn:
        runs, datasets = _target_scope(conn, tenant_id, mode)
        stats: dict[str, Any] = {
            "mode": mode, "dry_run": dry_run, "runs": len(runs), "datasets": len(datasets),
            "cases": 0, "reports": 0, "baselines": 0, "profiles": 0, "audits": 0, "objects_removed": 0,
        }
        if runs:
            stats["cases"] = fetch_all(
                conn, "SELECT count(*) AS n FROM eval_case_results WHERE tenant_id=%s AND run_id=ANY(%s)",
                (tenant_id, runs))[0]["n"]
            report_rows = fetch_all(
                conn, "SELECT tenant_id, object_key FROM eval_run_reports WHERE tenant_id=%s AND run_id=ANY(%s)",
                (tenant_id, runs))
        else:
            report_rows = []
        stats["reports"] = len(report_rows)
        stats["baselines"] = fetch_all(
            conn, "SELECT count(*) AS n FROM eval_baselines WHERE tenant_id=%s" +
            ("" if mode != "legacy" or not runs else " AND (run_id=ANY(%s) OR dataset_id=ANY(%s))"),
            (tenant_id,) if mode != "legacy" or not runs else (tenant_id, runs, datasets))[0]["n"]
        if mode in ("runs", "all"):
            stats["profiles"] = fetch_all(
                conn, "SELECT count(*) AS n FROM eval_profiles WHERE tenant_id=%s", (tenant_id,))[0]["n"]
        if include_audit:
            stats["audits"] = fetch_all(
                conn, "SELECT count(*) AS n FROM eval_audit_logs WHERE tenant_id=%s", (tenant_id,))[0]["n"]
        if dry_run:
            return stats

        store = get_blob_store()
        for row in report_rows:
            try:
                store.remove_blob(row["tenant_id"], row["object_key"])
                stats["objects_removed"] += 1
            except Exception:
                continue
        if mode == "legacy":
            conn.execute("DELETE FROM eval_baselines WHERE tenant_id=%s AND (run_id=ANY(%s) OR dataset_id=ANY(%s))",
                         (tenant_id, runs or [""], datasets or [""]))
        else:
            conn.execute("DELETE FROM eval_baselines WHERE tenant_id=%s", (tenant_id,))
        if runs:
            conn.execute("DELETE FROM eval_runs WHERE tenant_id=%s AND run_id=ANY(%s)", (tenant_id, runs))
        if mode in ("runs", "all"):
            conn.execute("DELETE FROM eval_profiles WHERE tenant_id=%s", (tenant_id,))
        if datasets:
            conn.execute("DELETE FROM eval_datasets WHERE dataset_id=ANY(%s)", (datasets,))
        if include_audit:
            conn.execute("DELETE FROM eval_audit_logs WHERE tenant_id=%s", (tenant_id,))
        evaluation_repo.insert_audit_log(
            conn, tenant_id, actor_id, "purge_data", mode,
            {"mode": mode, "runs": stats["runs"], "datasets": stats["datasets"], "cases": stats["cases"],
             "reports": stats["reports"], "baselines": stats["baselines"], "profiles": stats["profiles"],
             "audits": stats["audits"], "objects_removed": stats["objects_removed"]},
        )
    return stats