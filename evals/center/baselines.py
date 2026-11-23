"""评测中心基线：把指定批次锁定为某数据集版本 + 口径 + 方式的 active 基线。"""
from __future__ import annotations

from typing import Any

from taozhi.infrastructure.persistence import evaluation_repo


def set_baseline(conn: Any, tenant_id: str, actor_id: str, run_id: str, note: str) -> dict:
    """把指定批次设为基线，旧基线保留历史。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        actor_id: 操作账号编号。
        run_id: 批次编号。
        note: 基线备注。

    Returns:
        新建的基线行。

    Raises:
        ValueError: 批次不存在或未关联数据集时抛出。
    """
    run = evaluation_repo.run_by_id(conn, tenant_id, run_id)
    if run is None:
        raise ValueError(f"评测批次不存在：{run_id}")
    if not run["dataset_id"]:
        raise ValueError(f"评测批次未关联数据集，无法设为基线：{run_id}")
    return evaluation_repo.set_baseline(conn, tenant_id, actor_id, run=run, note=note)


def baseline_for_run(conn: Any, tenant_id: str, run_id: str) -> dict | None:
    """查询某批次已登记的基线行（active 或历史），供幂等回填判断。"""
    return evaluation_repo.fetch_one(
        conn,
        "SELECT baseline_id, eval_kind, dataset_id, dataset_version, scope, mode, run_id, note, active, created_at "
        "FROM eval_baselines WHERE tenant_id=%s AND run_id=%s ORDER BY created_at DESC, baseline_id DESC LIMIT 1",
        (tenant_id, run_id),
    )