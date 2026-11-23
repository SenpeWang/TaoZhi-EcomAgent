"""评测中心命令行：数据集登记、报告发布、基线设置、历史回填与入库自检。"""
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
from evals.center import baselines, datasets, publish
from taozhi.infrastructure.persistence.database import database_connection, fetch_all

SCOPES = publish.SCOPE_VALUES
MODES = publish.MODE_VALUES


def _tenant_code(args: argparse.Namespace) -> str:
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
    version = args.version or datasets.current_version()
    tenant_id, actor_id = _identity(args)
    with database_connection() as conn:
        rows = datasets.register(conn, version, tenant_id, actor_id)
    _print_json({"version": version, "datasets": [row["dataset_id"] for row in rows]})
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
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
    tenant_id, actor_id = _identity(args)
    with database_connection() as conn:
        row = baselines.set_baseline(conn, tenant_id, actor_id, args.run_id, args.note)
    _print_json({"baseline_id": row["baseline_id"], "run_id": row["run_id"],
                 "dataset_id": row["dataset_id"], "dataset_version": row["dataset_version"],
                 "scope": row["scope"], "mode": row["mode"]})
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    stats = backfill_module.backfill(tenant_code=_tenant_code(args),
                                     owner_username=args.owner_username or "owner",
                                     reset=args.reset)
    _print_json(stats)
    return 0


def _check_datasets(failures: list[str]) -> dict:
    version = datasets.current_version()
    checked = 0
    for suite in datasets.SUITES:
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
    return {"version": version, "checked_suites": checked}


def _run_check(tenant_code: str) -> tuple[list[str], dict]:
    """执行入库自检：描述符摘要、基线唯一性、报告对象与用例数一致性。"""
    failures: list[str] = []
    stats = _check_datasets(failures)
    from taozhi.infrastructure.knowledge.storage import get_blob_store

    tenant_id, _actor_id = publish.resolve_owner(tenant_code, "owner")
    store = get_blob_store()
    with database_connection() as conn:
        version = stats["version"]
        registered = fetch_all(
            conn, "SELECT dataset_id, suite, version, archived, case_count, cases_payload "
                  "FROM eval_datasets WHERE version=%s ORDER BY dataset_id", (version,))
        for row in registered:
            suite = row.get("suite") or ""
            try:
                actual = len(datasets.suite_cases(version, suite))
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
        })
    return failures, stats


def _cmd_purge(args: argparse.Namespace) -> int:
    """按口径清理历史版本与旧批次；默认只预演，确认后执行并留审计。"""
    tenant_id, actor_id = _identity(args)
    stats = purge_module.purge(tenant_id=tenant_id, actor_id=actor_id, mode=args.mode,
                               include_audit=args.include_audit, dry_run=not args.yes)
    _print_json(stats)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
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
          f"报告 {stats['reports_checked']}")
    _print_json({"ok": not failures, "failures": len(failures), **stats})
    return 1 if failures else 0


def _add_identity(parser: argparse.ArgumentParser, *, actor_name: str = "owner") -> None:
    parser.add_argument("--tenant-id", help="直接指定租户编号")
    parser.add_argument(f"--{actor_name}-id", help="直接指定操作账号编号")
    parser.add_argument("--tenant-code", help="按租户编码解析（默认 mofa）")
    parser.add_argument(f"--{actor_name}-username", help="按登录名解析（默认 owner）")


def build_parser() -> argparse.ArgumentParser:
    """构造评测中心命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="evals.center.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="登记数据集版本")
    register.add_argument("--version", help="数据集版本目录名，默认当前最新版本")
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