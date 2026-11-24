"""评测中心历史数据回填：登记 V1 数据集、发布历史报告、锁定基线与合并旧数据集。"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from evals.center import baselines, datasets, publish
from taozhi.infrastructure.persistence import evaluation_repo
from taozhi.infrastructure.persistence.database import database_connection, fetch_all, fetch_one

ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = ROOT / "evals" / "baseline"
REPORTS_DIR = ROOT / "evals" / "reports"
AGENT_REPORTS_DIR = REPORTS_DIR / "agent_eval"
LEGACY_RAG_BASELINES = ("20261021-182903.json", "20261117-001401.json")
_RAG_SUITES = ("rag_core", "rag_adversarial")


def _read_json(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"报告内容必须是 JSON 对象：{path.name}")
    return payload


def _store_if_missing(conn, tenant_id: str, result: dict, payload: bytes) -> None:
    """重复批次也补齐原始报告对象，保证 run 与 report 一一对应。"""
    publish.ensure_report_object(conn, tenant_id, result["run_id"], eval_kind=result["eval_kind"],
                                 dataset_id=result["dataset_id"], payload=payload)


def _ensure_baseline(conn, tenant_id: str, actor_id: str, run_id: str, note: str) -> bool:
    """批次已有基线留痕时跳过，保证重复回填不新增基线历史。"""
    if baselines.baseline_for_run(conn, tenant_id, run_id) is not None:
        return False
    baselines.set_baseline(conn, tenant_id, actor_id, run_id, note)
    return True


def _merge_legacy_agent_datasets(conn, tenant_id: str, actor_id: str, version: str) -> int:
    """把旧式数据集下的批次迁移到统一 V1 数据集并归档旧行。"""
    target = datasets.dataset_id(version, "agent_research")
    legacy_rows = fetch_all(
        conn,
        "SELECT dataset_id FROM eval_datasets "
        "WHERE dataset_id LIKE %s OR dataset_id LIKE %s "
        "ORDER BY dataset_id",
        ("agent_research-live-%", "agent_research-synthetic_trace_regrade-%"),
    )
    merged = 0
    for row in legacy_rows:
        moved = evaluation_repo.move_runs_to_dataset(conn, row["dataset_id"], target, version)
        evaluation_repo.retire_dataset(conn, row["dataset_id"])
        if moved:
            merged += moved
            evaluation_repo.insert_audit_log(
                conn, tenant_id, actor_id, "register_dataset", target,
                {"merged_dataset_id": row["dataset_id"], "moved_runs": moved, "dataset_version": version},
            )
    return merged


def backfill(tenant_code: str = "mofa", owner_username: str = "owner", reset: bool = False) -> dict:
    """回填历史评测数据，可重复执行（幂等）。

    Args:
        tenant_code: 企业租户编码。
        owner_username: 批次归属账号登录名。
        reset: 是否先清空评测中心写入表（仅显式传参时执行）。

    Returns:
        含数据集数、批次数、用例数、active 基线条数与跳过报告数的汇总。
    """
    tenant_id, actor_id = publish.resolve_owner(tenant_code, owner_username)
    version = datasets.current_version()
    skipped = 0
    duplicates = 0
    with database_connection() as conn:
        if reset:
            conn.execute(
                "TRUNCATE TABLE eval_case_results, eval_run_reports, eval_baselines, "
                "eval_runs, eval_datasets, eval_audit_logs"
            )
        datasets.register(conn, version, tenant_id, actor_id)

        first_baseline = BASELINE_DIR / LEGACY_RAG_BASELINES[0]
        report_git = _read_json(first_baseline).get("git") or ""
        for suite in _RAG_SUITES:
            datasets.ensure_legacy_dataset(conn, "rag", suite, report_git, source="live")

        rag_results: list[dict] = []
        seen_digests: set[str] = set()
        for name in LEGACY_RAG_BASELINES:
            path = BASELINE_DIR / name
            if not path.is_file():
                skipped += 1
                continue
            payload = path.read_bytes()
            seen_digests.add(sha256(payload).hexdigest())
            result = publish.publish_report(path, tenant_id=tenant_id, owner_id=actor_id, kind="rag",
                                            legacy=True, scope="full", mode="live",
                                            notes="历史 RAG 基线快照入库")
            if not result["created"]:
                _store_if_missing(conn, tenant_id, result, payload)
                duplicates += 1
            rag_results.append(result)

        for path in sorted(REPORTS_DIR.glob("*.json")):
            payload = path.read_bytes()
            digest = sha256(payload).hexdigest()
            if digest in seen_digests:
                skipped += 1
                continue
            try:
                result = publish.publish_report(path, tenant_id=tenant_id, owner_id=actor_id, kind="rag",
                                                legacy=True, notes="历史 RAG 报告回填")
            except (OSError, ValueError) as exc:
                skipped += 1
                print(f"跳过 RAG 报告 {path.name}（{exc}）")
                continue
            if not result["created"]:
                _store_if_missing(conn, tenant_id, result, payload)
                duplicates += 1
            rag_results.append(result)

        for path in sorted(AGENT_REPORTS_DIR.rglob("agent_eval_report_*.json")):
            try:
                raw = _read_json(path)
            except (OSError, ValueError, json.JSONDecodeError):
                skipped += 1
                continue
            schema = raw.get("schema_version")
            if not isinstance(schema, int) or schema < 3:
                skipped += 1
                continue
            payload = path.read_bytes()
            try:
                result = publish.publish_report(path, tenant_id=tenant_id, owner_id=actor_id, kind="agent",
                                                notes="历史 Agent 报告回填")
            except (OSError, ValueError) as exc:
                skipped += 1
                print(f"跳过 Agent 报告 {path.name}（{exc}）")
                continue
            if not result["created"]:
                _store_if_missing(conn, tenant_id, result, payload)
                duplicates += 1

        merged_runs = _merge_legacy_agent_datasets(conn, tenant_id, actor_id, version)

        rag_active = None
        if len(rag_results) >= 2:
            _ensure_baseline(conn, tenant_id, actor_id, rag_results[0]["run_id"], "RAG 历史原始基线（20261021）")
            _ensure_baseline(conn, tenant_id, actor_id, rag_results[1]["run_id"], "RAG 当前原始基线（20261117）")
            rag_active = rag_results[1]["run_id"]

        agent_baseline = None
        print("评测中心只接收真实执行批次，跳过 Agent 合成回放基线设置")

        stats = {
            "version": version,
            "datasets": fetch_one(conn, "SELECT count(*) AS n FROM eval_datasets")["n"],
            "datasets_active": fetch_one(conn, "SELECT count(*) AS n FROM eval_datasets WHERE NOT archived")["n"],
            "legacy_datasets": fetch_one(conn, "SELECT count(*) AS n FROM eval_datasets WHERE archived")["n"],
            "runs": fetch_one(conn, "SELECT count(*) AS n FROM eval_runs")["n"],
            "cases": fetch_one(conn, "SELECT count(*) AS n FROM eval_case_results")["n"],
            "reports": fetch_one(conn, "SELECT count(*) AS n FROM eval_run_reports")["n"],
            "profiles": fetch_one(conn, "SELECT count(*) AS n FROM eval_profiles")["n"],
            "active_baselines": fetch_one(
                conn, "SELECT count(*) AS n FROM eval_baselines WHERE tenant_id=%s AND active", (tenant_id,))["n"],
            "merged_legacy_runs": merged_runs,
            "rag_baseline": rag_active,
            "agent_baseline": agent_baseline,
            "skipped": skipped,
            "duplicates": duplicates,
        }
    print(f"数据集 {stats['datasets']}（active {stats['datasets_active']}，历史归档 {stats['legacy_datasets']}）"
          f"，批次 {stats['runs']}，用例 {stats['cases']}，基线 {stats['active_baselines']}")
    print(f"跳过报告 {stats['skipped']}，已存在批次 {stats['duplicates']}，合并旧批次 {stats['merged_legacy_runs']}")
    print(f"RAG active 基线 {stats['rag_baseline']}，Agent 基线 {stats['agent_baseline']}")
    return stats