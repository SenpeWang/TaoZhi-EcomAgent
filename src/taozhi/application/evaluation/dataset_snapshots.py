"""数据集内容快照：为评测运行固化不可变的套件用例与文件摘要。

快照按「评测类型 + 数据集版本」聚合该版本下全部已登记套件（RAG 全量因此同时覆盖
``rag_core`` 与 ``rag_adversarial``）。同一内容摘要复用同一快照；快照用例只读，
供入库校验、结果详情与历史追溯使用。本模块只做快照语义编排，SQL 全部委托
:mod:`taozhi.infrastructure.persistence.evaluation_repo`。
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from taozhi.infrastructure.persistence import evaluation_repo

_COVERAGE_SUITES: dict[tuple[str, str], tuple[str, ...]] = {
    ("agent", "full"): ("agent_research",),
    ("rag", "full"): ("rag_core", "rag_adversarial"),
    ("rag", "behavior"): ("rag_core",),
    ("rag", "adversarial"): ("rag_adversarial",),
}


def canonical_json(value: Any) -> bytes:
    """把用例/元数据序列化为规范化 JSON 字节。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def case_sha256(value: Any) -> str:
    """对用例定义求规范化摘要，与入库侧 case_sha256 口径一致。"""
    return sha256(canonical_json(value)).hexdigest()


def case_id_of(case: dict) -> str:
    """读取数据集用例编号（RAG 用 case_id，Agent 用 id）。"""
    value = case.get("case_id") or case.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("数据集用例缺少 case_id/id")
    return value


def snapshot_row(conn: Any, tenant_id: str, snapshot_id: str) -> dict | None:
    """按主键读取快照元信息（委托评测仓储）。"""
    return evaluation_repo.dataset_snapshot_row(conn, tenant_id, snapshot_id)


def snapshot_cases(conn: Any, tenant_id: str, snapshot_id: str) -> list[dict]:
    """读取快照下的全部原始用例（委托评测仓储，按套件与序号排序）。"""
    return evaluation_repo.dataset_snapshot_cases(conn, tenant_id, snapshot_id)


def ensure_snapshot(conn: Any, tenant_id: str, eval_kind: str, dataset_version: str,
                    primary_dataset_id: str, actor_id: str) -> dict:
    """创建或复用该版本内容快照，返回快照元信息。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        eval_kind: 评测类型（agent/rag）。
        dataset_version: 数据集版本。
        primary_dataset_id: 触发评测所选的登记数据集编号。
        actor_id: 创建人账号编号。

    Returns:
        快照行（含 snapshot_id、bundle_sha256、case_count）。

    Raises:
        ValueError: 版本下没有已登记数据集时抛出。
    """
    rows = evaluation_repo.dataset_version_rows(conn, eval_kind, dataset_version)
    if not rows:
        raise ValueError(f"数据集版本下没有已登记套件：{dataset_version}")
    if not any(row["dataset_id"] == primary_dataset_id for row in rows):
        raise ValueError(f"数据集版本与所选数据集不一致：{primary_dataset_id}")
    manifest = [
        {
            "dataset_id": row["dataset_id"],
            "suite": row["suite"],
            "content_sha256": row["content_sha256"] or "",
            "case_count": int(row["case_count"] or 0),
        }
        for row in rows
    ]
    bundle_sha256 = case_sha256({"eval_kind": eval_kind, "dataset_version": dataset_version,
                                 "suites": manifest})
    existing = evaluation_repo.dataset_snapshot_by_bundle(
        conn, tenant_id, eval_kind, dataset_version, bundle_sha256)
    if existing is not None:
        return existing

    files: dict[str, str] = {}
    for row in rows:
        files.update((row["metadata"] or {}).get("files") or {})
    snapshot_id = "snap_" + case_sha256({"tenant_id": tenant_id,
                                         "bundle_sha256": bundle_sha256})[:16]
    pending: list[dict] = []
    for row in rows:
        for ordinal, case in enumerate(row["cases_payload"] or []):
            pending.append({
                "snapshot_id": snapshot_id,
                "suite": row["suite"],
                "case_id": case_id_of(case),
                "case_sha256": case_sha256(case),
                "ordinal": ordinal,
                "case_payload": case,
            })
    evaluation_repo.insert_dataset_snapshot(
        conn, snapshot_id=snapshot_id, tenant_id=tenant_id, eval_kind=eval_kind,
        dataset_version=dataset_version, primary_dataset_id=primary_dataset_id,
        bundle_sha256=bundle_sha256, case_count=len(pending), files=files,
        metadata={"suites": manifest}, actor_id=actor_id)
    evaluation_repo.insert_dataset_snapshot_cases(conn, pending)
    return snapshot_row(conn, tenant_id, snapshot_id) or {}


_LAYER_PREFIXES = ("retrieval:", "behavior:", "adversarial:")


def dataset_case_id(case_id: str) -> str:
    """把批次逐例编号还原为数据集用例编号（去掉 RAG 层前缀）。"""
    for prefix in _LAYER_PREFIXES:
        if case_id.startswith(prefix):
            return case_id[len(prefix):]
    return case_id


def lookup_case(cases: list[dict], case_id: str) -> dict | None:
    """在快照用例中按数据集编号查找（RAG 跨套件编号唯一）。"""
    key = dataset_case_id(case_id)
    for row in cases:
        if row["case_id"] == key:
            return row
    return None


def next_run_seq(conn: Any, tenant_id: str, dataset_id: str, eval_kind: str, scope: str,
                 mode: str) -> int:
    """读取同一分组内下一个运行序号（委托评测仓储，调用方保证事务与并发约束）。"""
    return evaluation_repo.next_run_seq(conn, tenant_id, dataset_id, eval_kind, scope, mode)


def validate_report_cases(rows: list[dict], cases: list[dict], eval_kind: str,
                          scope: str, mode: str) -> dict:
    """校验逐例结果与快照一致；full 口径要求覆盖对应套件全部用例。

    Args:
        rows: 入库逐例结果（含 case_id、case_sha256、suite）。
        cases: 快照原始用例列表。
        eval_kind: 评测类型。
        scope: 评测口径。
        mode: 执行方式，Agent 全量口径据此筛选应覆盖用例。

    Returns:
        覆盖统计：{"expected": n, "covered": m, "covered_case_ids": [...]}。

    Raises:
        ValueError: 出现未知用例、摘要不一致或 full 口径覆盖不全时抛出。
    """
    by_id = {row["case_id"]: row for row in cases}
    covered: set[str] = set()
    for row in rows:
        case_id = dataset_case_id(row["case_id"])
        snapshot_case = by_id.get(case_id)
        if snapshot_case is None:
            raise ValueError(f"评测结果包含数据集快照中不存在的用例：{row['case_id']}")
        if row.get("case_sha256") and row["case_sha256"] != snapshot_case["case_sha256"]:
            raise ValueError(f"用例定义摘要与快照不一致：{row['case_id']}")
        covered.add(case_id)
    expected_suites = _COVERAGE_SUITES.get((eval_kind, scope), ())
    expected_rows = [row for row in cases if row["suite"] in expected_suites] \
        if expected_suites else []
    if eval_kind == "agent":
        expected_rows = [
            row for row in expected_rows
            if (row["case_payload"] or {}).get("execution_mode", "live") == mode
        ]
    expected_ids = {row["case_id"] for row in expected_rows}
    missing = sorted(expected_ids - covered)
    if missing:
        raise ValueError(f"full 口径缺少用例 {len(missing)} 个，例如：{missing[:3]}")
    return {"expected": len(expected_ids) if expected_suites else len(covered),
            "covered": len(covered), "covered_case_ids": sorted(covered)}


def backfill_snapshots(conn: Any, tenant_id: str, actor_id: str, *, dry_run: bool = True) -> dict:
    """为缺少快照的存量批次补齐快照与运行序号。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        actor_id: 执行人账号编号。
        dry_run: 为 True 时只统计，不写入。

    Returns:
        统计：{"runs": n, "snapshots": n, "skipped": n, "mismatched": n, "dry_run": bool}。
    """
    rows = evaluation_repo.runs_missing_snapshot(conn, tenant_id)
    stats = {"runs": len(rows), "snapshots": 0, "skipped": 0, "mismatched": 0,
             "dry_run": dry_run}
    groups: dict[tuple, int] = {}
    for row in rows:
        dataset = evaluation_repo.dataset_row(conn, row["dataset_id"]) if row["dataset_id"] else None
        if dataset is None or not row["dataset_version"]:
            stats["skipped"] += 1
            continue
        recorded_sha = row.get("dataset_sha256") or ""
        current_sha = dataset.get("content_sha256") or ""
        if not recorded_sha or not current_sha or recorded_sha != current_sha:
            stats["mismatched"] += 1
            print(f"跳过批次 {row['run_id']}：运行数据摘要与当前数据集不一致")
            continue
        snapshot = ensure_snapshot(
            conn, tenant_id, row["eval_kind"], row["dataset_version"],
            row["dataset_id"], actor_id)
        key = (row["dataset_id"], row["eval_kind"], row["scope"], row["mode"])
        if key not in groups:
            groups[key] = evaluation_repo.next_run_seq(
                conn, tenant_id, row["dataset_id"], row["eval_kind"], row["scope"], row["mode"])
        else:
            groups[key] += 1
        if not dry_run:
            evaluation_repo.set_run_snapshot(
                conn, tenant_id, row["run_id"], snapshot["snapshot_id"], groups[key])
            evaluation_repo.insert_audit_log(
                conn, tenant_id, actor_id, "publish_run", row["run_id"],
                {"backfill": "dataset_snapshot", "snapshot_id": snapshot["snapshot_id"],
                 "run_seq": groups[key]})
        stats["snapshots"] += 1
    return stats