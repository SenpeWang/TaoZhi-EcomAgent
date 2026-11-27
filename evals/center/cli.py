"""评测中心命令行：数据集登记、报告发布、基线设置、历史版本迁移、历史回填与入库自检。"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any

from evals.center import backfill as backfill_module
from evals.center import purge as purge_module
from evals.center import baselines, datasets, publish, version_migration
from taozhi.application.evaluation import dataset_snapshots as snapshots
from taozhi.infrastructure.persistence.database import database_connection, fetch_all

SCOPES = publish.SCOPE_VALUES
MODES = publish.MODE_VALUES


def _tenant_code(args: argparse.Namespace) -> str:
    """解析租户编码：命令行参数优先，其次环境变量，最后 mofa。

    Args:
        args: 命令行参数。

    Returns:
        租户编码。
    """
    return getattr(args, "tenant_code", None) or os.environ.get("ECOM_TENANT_CODE") or "mofa"


def _identity(args: argparse.Namespace) -> tuple[str, str]:
    """解析租户与账号编号：显式 ID 优先，否则按租户编码 + 登录名查询。"""
    tenant_id = getattr(args, "tenant_id", None)
    owner_id = getattr(args, "owner_id", None) or getattr(args, "actor_id", None)
    if tenant_id or owner_id:
        if not (tenant_id and owner_id):
            raise ValueError("--tenant-id 与 --owner-id/--actor-id 需要同时提供")
        return tenant_id, owner_id
    username = (getattr(args, "owner_username", None) or getattr(args, "actor_username", None)
                or os.environ.get("ECOM_OWNER_USERNAME") or "owner")
    return publish.resolve_owner(_tenant_code(args), username)


def _print_json(payload: dict) -> None:
    """把结果以单行 JSON 打印到标准输出。

    Args:
        payload: 待输出的结果字典。
    """
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _collect_reports(directory: Path, kind: str) -> list[Path]:
    """按报告类型枚举目录内可入库的报告文件，保持路径有序。"""
    root = Path(directory)
    agent_paths = sorted(root.rglob("agent_eval_report_*.json"))
    if kind == "rag":
        return sorted(root.glob("*.json"))
    if kind == "agent":
        return agent_paths
    paths = sorted(root.glob("*.json")) + agent_paths
    seen: set[Path] = set()
    ordered = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _cmd_register(args: argparse.Namespace) -> int:
    """执行数据集登记子命令，输出登记版本与数据集编号。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码 0。
    """
    versions: dict[str, str] = {}
    if args.version:
        versions["rag"] = args.version
        versions["agent"] = args.version
    if args.rag_version:
        versions["rag"] = args.rag_version
    if args.agent_version:
        versions["agent"] = args.agent_version
    tenant_id, actor_id = _identity(args)
    with database_connection() as conn:
        rows = datasets.register(conn, versions or None, tenant_id, actor_id, refresh=args.refresh)
    resolved = {suite: datasets.current_version(suite) for suite in datasets.SUITES}
    _print_json({"versions": resolved, "datasets": [row["dataset_id"] for row in rows]})
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    """执行报告发布子命令，逐份入库并汇总失败数。

    Args:
        args: 命令行参数。

    Returns:
        有失败返回 2，否则返回 0。
    """
    tenant_id, owner_id = _identity(args)
    if args.report is None and args.reports_dir is None:
        raise ValueError("请指定 --report 或 --reports-dir")
    paths = [Path(args.report)] if args.report is not None else _collect_reports(args.reports_dir, args.kind)
    if not paths:
        raise ValueError(f"目录内没有可入库的报告：{args.reports_dir}")
    created = duplicated = failed = 0
    results: list[dict] = []
    for path in paths:
        try:
            result = publish.publish_report(
                path, tenant_id=tenant_id, owner_id=owner_id, kind=args.kind,
                profile_id=args.profile_id, mark_baseline=args.baseline,
                dataset_id=args.dataset_id, dataset_version=args.dataset_version,
                scope=args.scope, mode=args.mode, legacy=args.legacy, notes=args.notes,
                snapshot_id=args.snapshot_id or None, run_seq=args.run_seq,
                parent_run_id=args.parent_run_id or None,
                trigger_note=args.trigger_note or None,
            )
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"失败 {path.name}（{exc}）")
            continue
        results.append(result)
        if result["created"]:
            created += 1
            print(f"已入库 {path.name} 批次 {result['run_id']}")
        else:
            duplicated += 1
            print(f"已存在 {path.name} 批次 {result['run_id']}")
    if args.report is not None and len(results) == 1:
        summary = results[0]
    else:
        summary = {"total": len(paths), "created": created, "duplicate": duplicated, "failed": failed}
    _print_json(summary)
    return 2 if failed else 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    """执行基线锁定子命令并输出基线摘要。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码 0。
    """
    tenant_id, actor_id = _identity(args)
    with database_connection() as conn:
        row = baselines.set_baseline(conn, tenant_id, actor_id, args.run_id, args.note)
    _print_json({"baseline_id": row["baseline_id"], "run_id": row["run_id"],
                 "dataset_id": row["dataset_id"], "dataset_version": row["dataset_version"],
                 "scope": row["scope"], "mode": row["mode"]})
    return 0


def _cmd_migrate_versions(args: argparse.Namespace) -> int:
    """历史数据集版本前缀迁移；默认 dry-run，--apply 才写库。"""
    with database_connection() as conn:
        result = version_migration.apply(conn) if args.apply else version_migration.plan(conn)
    _print_json(result)
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    """执行历史评测数据回填子命令。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码 0。
    """
    stats = backfill_module.backfill(tenant_code=_tenant_code(args),
                                     owner_username=args.owner_username or "owner",
                                     reset=args.reset)
    _print_json(stats)
    return 0


def _check_datasets(failures: list[str]) -> dict:
    """校验最新版本各套件的描述符摘要与用例数。

    Args:
        failures: 失败信息收集列表。

    Returns:
        含版本与通过套件数的统计字典。
    """
    versions = {suite: datasets.current_version(suite) for suite in datasets.SUITES}
    checked = 0
    for suite in datasets.SUITES:
        version = versions[suite]
        try:
            definition = datasets.validate_suite(version, suite)
            cases = datasets.suite_cases(version, suite)
        except ValueError as exc:
            failures.append(f"数据集 {suite}（{version}）内容校验失败：{exc}")
            continue
        expected = int(definition.get("case_count") or 0)
        if expected and len(cases) != expected:
            failures.append(f"数据集 {suite} 用例数 {len(cases)} 与描述符 {expected} 不一致")
        checked += 1
    return {"version": versions["rag_core"], "versions": versions, "checked_suites": checked}


def _check_run_snapshots(conn: Any, tenant_id: str, failures: list[str]) -> dict:
    """校验批次与数据集内容快照的关联、用例数、运行序号与 legacy 豁免。

    非 legacy 批次必须绑定存在的快照且快照用例数大于零；绑定快照的批次必须有
    运行序号，且同一（数据集、类型、口径、方式）分组内序号唯一。

    Args:
        conn: 数据库连接。
        tenant_id: 租户编号。
        failures: 失败信息收集列表，原地追加。

    Returns:
        统计：{"runs_checked": n, "runs_with_snapshot": n, "snapshots_checked": n,
        "snapshot_missing": n, "runs_without_snapshot": n, "legacy_runs": n,
        "run_seq_duplicates": n}。
    """
    rows = fetch_all(conn, """
        SELECT r.run_id, r.dataset_id, r.dataset_version, r.eval_kind, r.scope, r.mode,
               r.dataset_snapshot_id, r.run_seq,
               s.snapshot_id AS joined_snapshot_id, s.case_count AS snapshot_case_count,
               COALESCE(d.archived, false) AS dataset_archived
        FROM eval_runs r
        LEFT JOIN eval_dataset_snapshots s ON s.snapshot_id = r.dataset_snapshot_id
        LEFT JOIN eval_datasets d ON d.dataset_id = r.dataset_id
        WHERE r.tenant_id=%s
        ORDER BY r.run_id
    """, (tenant_id,))
    stats = {
        "runs_checked": len(rows),
        "runs_with_snapshot": 0,
        "snapshots_checked": 0,
        "snapshot_missing": 0,
        "runs_without_snapshot": 0,
        "legacy_runs": 0,
        "run_seq_duplicates": 0,
    }
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        legacy = (bool(row.get("dataset_archived"))
                  or row.get("dataset_version") == datasets.LEGACY_VERSION)
        snapshot_id = row.get("dataset_snapshot_id")
        if legacy:
            stats["legacy_runs"] += 1
            if not snapshot_id:
                continue
        if not snapshot_id:
            stats["runs_without_snapshot"] += 1
            failures.append(f"批次 {row['run_id']} 未关联数据集内容快照（非 legacy 批次必须绑定快照）")
            continue
        stats["runs_with_snapshot"] += 1
        if row.get("joined_snapshot_id") is None:
            stats["snapshot_missing"] += 1
            failures.append(f"批次 {row['run_id']} 的数据集内容快照不存在：{snapshot_id}")
            continue
        stats["snapshots_checked"] += 1
        if int(row.get("snapshot_case_count") or 0) <= 0:
            failures.append(f"批次 {row['run_id']} 的数据集内容快照 {snapshot_id} 用例数为 0")
        if row.get("run_seq") is None:
            failures.append(f"批次 {row['run_id']} 已绑定快照但缺少运行序号 run_seq")
            continue
        key = (row.get("dataset_id"), row.get("eval_kind"), row.get("scope"), row.get("mode"))
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        seqs: dict[int, list[str]] = {}
        for row in group:
            seqs.setdefault(int(row["run_seq"]), []).append(row["run_id"])
        for seq in sorted(seqs):
            if len(seqs[seq]) > 1:
                stats["run_seq_duplicates"] += 1
                failures.append(f"分组 {key} 运行序号 {seq} 重复：{'、'.join(seqs[seq])}")
    return stats


def _run_check(tenant_code: str) -> tuple[list[str], dict]:
    """执行入库自检：描述符摘要、快照一致性、基线唯一性、报告对象与用例数一致性。

    Args:
        tenant_code: 租户编码，用于解析租户与账号编号。

    Returns:
        (失败信息列表, 统计字典)；无失败信息表示自检通过。
    """
    failures: list[str] = []
    stats = _check_datasets(failures)
    from taozhi.infrastructure.knowledge.storage import get_blob_store

    tenant_id, _actor_id = publish.resolve_owner(tenant_code, "owner")
    store = get_blob_store()
    with database_connection() as conn:
        versions = stats["versions"]
        registered: list[dict] = []
        for suite in datasets.SUITES:
            registered.extend(fetch_all(
                conn, "SELECT dataset_id, suite, version, archived, case_count, cases_payload "
                      "FROM eval_datasets WHERE version=%s AND suite=%s ORDER BY dataset_id",
                (versions[suite], suite)))
        for row in registered:
            suite = row.get("suite") or ""
            try:
                actual = len(datasets.suite_cases(row["version"], suite))
            except ValueError as exc:
                failures.append(f"数据集 {row['dataset_id']} 用例装载失败：{exc}")
                continue
            if int(row.get("case_count") or 0) != actual:
                failures.append(f"数据集 {row['dataset_id']} 登记用例数 {row.get('case_count')} "
                                f"与用例集 {actual} 不一致")
            payload = row.get("cases_payload") or []
            if payload and len(payload) != actual:
                failures.append(f"数据集 {row['dataset_id']} 用例 payload 数 {len(payload)} 不一致")

        duplicate_keys = fetch_all(
            conn,
            "SELECT eval_kind, dataset_version, scope, mode, count(*) AS n FROM eval_baselines "
            "WHERE tenant_id=%s AND active GROUP BY eval_kind, dataset_version, scope, mode "
            "HAVING count(*)>1", (tenant_id,))
        for row in duplicate_keys:
            failures.append(f"基线键存在多条 active：{row['eval_kind']}/{row['dataset_version']}/"
                            f"{row['scope']}/{row['mode']}（{row['n']} 条）")

        missing_runs = fetch_all(
            conn,
            "SELECT b.baseline_id FROM eval_baselines b LEFT JOIN eval_runs r "
            "ON (r.tenant_id, r.run_id)=(b.tenant_id, b.run_id) "
            "WHERE b.tenant_id=%s AND r.run_id IS NULL", (tenant_id,))
        for row in missing_runs:
            failures.append(f"基线批次不存在：{row['baseline_id']}")

        reports = fetch_all(
            conn, "SELECT tenant_id, run_id, object_key, sha256 FROM eval_run_reports ORDER BY run_id")
        for record in reports:
            try:
                payload = store.open_blob(record["tenant_id"], record["object_key"])
            except Exception as exc:
                failures.append(f"原始报告不可读：{record['run_id']}（{type(exc).__name__}）")
                continue
            if sha256(payload).hexdigest() != record["sha256"]:
                failures.append(f"原始报告摘要不匹配：{record['run_id']}")

        orphan_runs = fetch_all(
            conn,
            "SELECT r.run_id FROM eval_runs r LEFT JOIN eval_run_reports p "
            "ON (p.tenant_id, p.run_id)=(r.tenant_id, r.run_id) "
            "WHERE r.tenant_id=%s AND p.run_id IS NULL ORDER BY r.run_id", (tenant_id,))
        for row in orphan_runs:
            failures.append(f"批次缺少原始报告：{row['run_id']}")

        snapshot_stats = _check_run_snapshots(conn, tenant_id, failures)
        stats.update({
            "datasets": len(registered),
            "runs": fetch_all(conn, "SELECT count(*) AS n FROM eval_runs WHERE tenant_id=%s",
                              (tenant_id,))[0]["n"],
            "cases": fetch_all(conn, "SELECT count(*) AS n FROM eval_case_results WHERE tenant_id=%s",
                               (tenant_id,))[0]["n"],
            "baselines_active": fetch_all(
                conn, "SELECT count(*) AS n FROM eval_baselines WHERE tenant_id=%s AND active",
                (tenant_id,))[0]["n"],
            "reports_checked": len(reports),
            "orphan_runs": len(orphan_runs),
            **snapshot_stats,
        })
    return failures, stats


def _cmd_purge(args: argparse.Namespace) -> int:
    """按口径清理历史版本与旧批次；默认只预演，确认后执行并留审计。"""
    tenant_id, actor_id = _identity(args)
    stats = purge_module.purge(tenant_id=tenant_id, actor_id=actor_id, mode=args.mode,
                               include_audit=args.include_audit, dry_run=not args.yes)
    _print_json(stats)
    return 0


def _cmd_backfill_snapshots(args: argparse.Namespace) -> int:
    """执行数据集快照回填子命令（默认只预演）。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码 0。
    """
    tenant_id, actor_id = _identity(args)
    with database_connection() as conn:
        stats = snapshots.backfill_snapshots(
            conn, tenant_id, actor_id, dry_run=not args.yes)
    _print_json(stats)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """执行入库自检子命令，打印失败项与统计并输出 JSON 结论。

    Args:
        args: 命令行参数。

    Returns:
        自检未通过返回 1，否则返回 0。
    """
    try:
        failures, stats = _run_check(_tenant_code(args))
    except (OSError, ValueError) as exc:
        print(f"自检执行失败：{exc}")
        _print_json({"ok": False, "failures": 1})
        return 1
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"自检{'通过' if not failures else '未通过'}：数据集 {stats['checked_suites']} 个套件、"
          f"批次 {stats['runs']}、用例 {stats['cases']}、active 基线 {stats['baselines_active']}、"
          f"报告 {stats['reports_checked']}、快照 {stats['runs_with_snapshot']} 个批次"
          f"（缺失 {stats['snapshot_missing']}、未绑定 {stats['runs_without_snapshot']}、"
          f"legacy {stats['legacy_runs']}、序号重复 {stats['run_seq_duplicates']}）")
    _print_json({"ok": not failures, "failures": len(failures), **stats})
    return 1 if failures else 0


def _add_identity(parser: argparse.ArgumentParser, *, actor_name: str = "owner") -> None:
    """向子命令参数解析器追加租户与账号身份参数。

    Args:
        parser: 子命令参数解析器。
        actor_name: 操作账号参数名。
    """
    parser.add_argument("--tenant-id", help="直接指定租户编号")
    parser.add_argument(f"--{actor_name}-id", help="直接指定操作账号编号")
    parser.add_argument("--tenant-code", help="按租户编码解析（默认 mofa）")
    parser.add_argument(f"--{actor_name}-username", help="按登录名解析（默认 owner）")


def build_parser() -> argparse.ArgumentParser:
    """构造评测中心命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="evals.center.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="登记数据集版本")
    register.add_argument("--version", help="同时指定 RAG 与 Agent 的数据集版本")
    register.add_argument("--rag-version", help="RAG 数据集版本，默认最新 rag_v*")
    register.add_argument("--agent-version", help="Agent 数据集版本，默认最新 agent_v*")
    register.add_argument("--refresh", action="store_true",
                          help="用仓库原始用例覆盖已登记 payload（存量数据透明化刷新）")
    _add_identity(register, actor_name="actor")
    register.add_argument("--owner-username", dest="owner_username", help=argparse.SUPPRESS)
    register.set_defaults(handler=_cmd_register)

    publish_cmd = sub.add_parser("publish", help="发布评测报告入库")
    publish_cmd.add_argument("--kind", choices=("auto", "agent", "rag"), default="auto")
    publish_cmd.add_argument("--report", type=Path, help="单份报告路径")
    publish_cmd.add_argument("--reports-dir", type=Path, help="批量报告目录")
    publish_cmd.add_argument("--profile-id", help="指定算法版本编号")
    publish_cmd.add_argument("--baseline", action="store_true", help="入库后锁定为基线")
    publish_cmd.add_argument("--legacy", action="store_true", help="按历史版本归档（内容摘要不可验证）")
    publish_cmd.add_argument("--dataset-id", help="指定数据集编号")
    publish_cmd.add_argument("--dataset-version", help="指定数据集版本")
    publish_cmd.add_argument("--scope", choices=SCOPES, help="评测口径")
    publish_cmd.add_argument("--mode", choices=MODES, help="执行方式")
    publish_cmd.add_argument("--notes", default="", help="入库与基线备注")
    publish_cmd.add_argument("--snapshot-id", default="", help="触发阶段的数据集内容快照编号")
    publish_cmd.add_argument("--run-seq", type=int, default=None, help="触发阶段分配的运行序号")
    publish_cmd.add_argument("--parent-run-id", default="", help="重跑来源批次编号")
    publish_cmd.add_argument("--trigger-note", default="", help="触发说明")
    publish_cmd.add_argument("--tenant-id")
    publish_cmd.add_argument("--owner-id")
    publish_cmd.add_argument("--tenant-code")
    publish_cmd.add_argument("--owner-username")
    publish_cmd.set_defaults(handler=_cmd_publish)

    baseline = sub.add_parser("baseline", help="把批次设为基线")
    baseline.add_argument("--run-id", required=True)
    baseline.add_argument("--note", default="")
    _add_identity(baseline, actor_name="actor")
    baseline.set_defaults(handler=_cmd_baseline)

    migrate = sub.add_parser("migrate-versions", help="历史数据集版本补 rag_/agent_ 前缀（默认 dry-run）")
    migrate.add_argument("--apply", action="store_true", help="确认执行；缺省只输出迁移计划")
    migrate.set_defaults(handler=_cmd_migrate_versions)

    backfill = sub.add_parser("backfill", help="回填历史评测数据")
    backfill.add_argument("--tenant-code")
    backfill.add_argument("--owner-username")
    backfill.add_argument("--reset", action="store_true", help="先清空评测中心写入表（破坏性）")
    backfill.set_defaults(handler=_cmd_backfill)

    purge_cmd = sub.add_parser("purge", help="清理历史版本与旧批次（默认预演）")
    purge_cmd.add_argument("--mode", choices=purge_module.MODES, default="legacy",
                           help="legacy=归档数据集；runs=全部批次与算法版本；all=同时删数据集")
    purge_cmd.add_argument("--include-audit", action="store_true", help="同时删除审计留痕")
    purge_cmd.add_argument("--yes", action="store_true", help="确认执行；缺省只预演")
    _add_identity(purge_cmd, actor_name="actor")
    purge_cmd.set_defaults(handler=_cmd_purge)

    backfill_snapshots = sub.add_parser(
        "backfill-snapshots", help="为存量批次补齐数据集内容快照与运行序号（默认预演）")
    backfill_snapshots.add_argument("--yes", action="store_true", help="确认执行；缺省只预演")
    _add_identity(backfill_snapshots, actor_name="actor")
    backfill_snapshots.set_defaults(handler=_cmd_backfill_snapshots)

    check = sub.add_parser("check", help="校验数据集、基线与报告对象一致性")
    check.add_argument("--tenant-code")
    check.set_defaults(handler=_cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行入口，返回进程退出码。"""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ValueError as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"文件读写失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())