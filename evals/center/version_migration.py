"""评测中心历史数据集版本前缀迁移：v1…/v2…/v3… → rag_v…/agent_v…。

只处理版本号缺少体系前缀的存量行；已是 `rag_`/`agent_` 前缀的行保持不动。
默认 dry-run 只返回计划；`apply` 在调用方事务内完成迁移并写审计。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from taozhi.infrastructure.persistence import evaluation_repo

PREFIX_BY_KIND = {"rag": "rag_", "agent": "agent_"}


def target_version(eval_kind: str, version: str) -> str:
    """返回带体系前缀的版本号；已带前缀时原样返回。"""
    prefix = PREFIX_BY_KIND.get(eval_kind)
    if prefix is None:
        raise ValueError(f"未知评测类型：{eval_kind}")
    if version.startswith(("rag_", "agent_")):
        return version
    return f"{prefix}{version}"


def target_dataset_id(suite: str, version: str) -> str:
    """由套件与带前缀版本生成数据集编号。"""
    return f"{suite}-{version}"


def rewrite_files(eval_kind: str, files: dict[str, Any]) -> dict[str, Any]:
    """把旧 metadata.files 的 `rag/`、`agent/` 前缀去掉，匹配新版本目录。"""
    prefix = f"{eval_kind}/"
    return {key[len(prefix):] if key.startswith(prefix) else key: value
            for key, value in files.items()}


def _old_datasets(conn: Any) -> list[dict]:
    """列出尚未带 rag_/agent_ 前缀的历史数据集。

    Args:
        conn: 数据库连接。

    Returns:
        待迁移的数据集行列表。
    """
    return evaluation_repo.fetch_all(conn, """
        SELECT * FROM eval_datasets
        WHERE LEFT(version, 4) <> 'rag_' AND LEFT(version, 6) <> 'agent_'
        ORDER BY eval_kind, version, suite
    """)


def _children(conn: Any, dataset_id: str) -> dict[str, int]:
    """统计数据集下的批次与基线引用数。

    Args:
        conn: 数据库连接。
        dataset_id: 数据集编号。

    Returns:
        含 runs 与 baselines 计数的字典。
    """
    runs = evaluation_repo.fetch_one(conn, "SELECT count(*) AS n FROM eval_runs WHERE dataset_id=%s",
                                     (dataset_id,))
    baselines = evaluation_repo.fetch_one(conn, "SELECT count(*) AS n FROM eval_baselines WHERE dataset_id=%s",
                                          (dataset_id,))
    return {"runs": int(runs["n"]) if runs else 0, "baselines": int(baselines["n"]) if baselines else 0}


def plan(conn: Any) -> dict:
    """只读生成迁移计划：新建/合并的数据集、需更新的子表与旧行。"""
    operations = []
    for row in _old_datasets(conn):
        version = target_version(row["eval_kind"], row["version"])
        dataset_id = target_dataset_id(row["suite"], version)
        existing = evaluation_repo.dataset_row(conn, dataset_id)
        children = _children(conn, row["dataset_id"])
        operations.append({
            "from_dataset_id": row["dataset_id"],
            "from_version": row["version"],
            "to_dataset_id": dataset_id,
            "to_version": version,
            "action": "merge" if existing is not None else "create",
            "runs": children["runs"],
            "baselines": children["baselines"],
        })
    profiles = evaluation_repo.fetch_all(conn, """
        SELECT eval_kind, dataset_version, count(*) AS n
        FROM eval_profiles
        WHERE LEFT(dataset_version, 4) <> 'rag_' AND LEFT(dataset_version, 6) <> 'agent_'
        GROUP BY eval_kind, dataset_version ORDER BY eval_kind, dataset_version
    """)
    return {
        "apply": False,
        "datasets": operations,
        "profiles": [{
            "eval_kind": item["eval_kind"],
            "from_version": item["dataset_version"],
            "to_version": target_version(item["eval_kind"], item["dataset_version"]),
            "count": int(item["n"]),
        } for item in profiles],
    }


def apply(conn: Any) -> dict:
    """执行迁移：创建/合并数据集、重挂批次/基线/算法版本，删旧行并写审计。"""
    operations = []
    affected: dict[str, dict] = {}
    for row in _old_datasets(conn):
        eval_kind = row["eval_kind"]
        version = target_version(eval_kind, row["version"])
        dataset_id = target_dataset_id(row["suite"], version)
        existing = evaluation_repo.dataset_row(conn, dataset_id)
        children_before = _children(conn, row["dataset_id"])
        if existing is None:
            metadata = dict(row.get("metadata") or {})
            metadata["files"] = rewrite_files(eval_kind, dict(metadata.get("files") or {}))
            evaluation_repo.upsert_dataset(conn, {
                "dataset_id": dataset_id,
                "name": row["name"],
                "eval_kind": eval_kind,
                "version": version,
                "description": row.get("description") or "",
                "source": row.get("source") or "live",
                "suite": row["suite"],
                "archived": bool(row.get("archived")),
                "content_sha256": row.get("content_sha256") or "",
                "case_count": int(row.get("case_count") or 0),
                "cases_payload": row.get("cases_payload") or [],
                "metadata": metadata,
                "schema_version": int(row.get("schema_version") or 1),
            })
            action = "create"
        else:
            action = "merge"
        moved = evaluation_repo.move_runs_to_dataset(conn, row["dataset_id"], dataset_id, version)
        baselines = conn.execute(
            "UPDATE eval_baselines SET dataset_id=%s, dataset_version=%s WHERE dataset_id=%s",
            (dataset_id, version, row["dataset_id"]),
        ).rowcount
        remaining = _children(conn, row["dataset_id"])
        if remaining["runs"] or remaining["baselines"]:
            raise ValueError(f"{row['dataset_id']} 仍有子引用，迁移中止")
        conn.execute("DELETE FROM eval_datasets WHERE dataset_id=%s", (row["dataset_id"],))
        operations.append({
            "from_dataset_id": row["dataset_id"],
            "to_dataset_id": dataset_id,
            "to_version": version,
            "action": action,
            "runs": moved,
            "baselines": baselines,
        })
        for item in evaluation_repo.fetch_all(
                conn, "SELECT DISTINCT tenant_id, owner_id FROM eval_runs WHERE dataset_id=%s",
                (dataset_id,)):
            affected.setdefault(item["tenant_id"], item["owner_id"])

    profiles = conn.execute("""
        UPDATE eval_profiles
        SET dataset_version = CASE eval_kind
            WHEN 'rag' THEN 'rag_' || dataset_version
            WHEN 'agent' THEN 'agent_' || dataset_version
            ELSE dataset_version END
        WHERE LEFT(dataset_version, 4) <> 'rag_' AND LEFT(dataset_version, 6) <> 'agent_'
    """).rowcount

    for tenant_id, owner_id in affected.items():
        evaluation_repo.insert_audit_log(
            conn, tenant_id, owner_id, "migrate_dataset_versions", tenant_id,
            {"datasets": operations, "profiles_updated": profiles},
        )
    return {"apply": True, "datasets": operations, "profiles_updated": profiles}