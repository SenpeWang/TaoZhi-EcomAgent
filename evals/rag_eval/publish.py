"""把 RAG 三层评测报告归档到评测中心。"""
from __future__ import annotations

from taozhi.config import DEFAULT_TENANT_CODE

import argparse
import json
import os
from pathlib import Path

from evals.center import publish

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def main() -> None:
    """命令行入口：把 RAG 报告发布到评测中心。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="单份 RAG 报告路径")
    parser.add_argument("--reports-dir", type=Path, help="批量导入目录，默认 evals/reports")
    parser.add_argument("--tenant-id")
    parser.add_argument("--owner-id")
    parser.add_argument("--tenant-code", default=None, help="按租户编码解析归属（默认 mofa）")
    parser.add_argument("--owner-username", default="owner")
    parser.add_argument("--profile-id", help="显式指定算法版本编号")
    parser.add_argument("--baseline", action="store_true", help="入库后锁定为基线")
    parser.add_argument("--legacy", action="store_true", help="按历史版本归档（内容摘要不可验证）")
    args = parser.parse_args()
    try:
        if args.tenant_id and args.owner_id:
            tenant_id, owner_id = args.tenant_id, args.owner_id
        else:
            tenant_id, owner_id = publish.resolve_owner(
                args.tenant_code or os.environ.get("ECOM_TENANT_CODE", DEFAULT_TENANT_CODE), args.owner_username)
    except ValueError as exc:
        parser.exit(2, f"归属解析失败（{exc}）\n")
    if args.report is not None:
        paths = [args.report]
    else:
        paths = sorted((args.reports_dir or DEFAULT_REPORTS_DIR).glob("*.json"))
    if not paths:
        parser.exit(2, "没有可入库的 RAG 报告\n")
    created = duplicated = skipped = failed = 0
    for path in paths:
        try:
            result = publish.publish_report(
                path, tenant_id=tenant_id, owner_id=owner_id, kind="rag",
                profile_id=args.profile_id, mark_baseline=args.baseline, legacy=args.legacy)
        except OSError as exc:
            failed += 1
            print(f"读取失败 {path.name}（{exc}）")
            continue
        except ValueError as exc:
            skipped += 1
            print(f"跳过 {path.name}（{exc}）")
            continue
        if result["created"]:
            created += 1
            print(f"已入库 {path.name} 批次 {result['run_id']}")
        else:
            duplicated += 1
            print(f"已存在 {path.name} 批次 {result['run_id']}")
    summary = {"created": created, "duplicate": duplicated, "skipped": skipped, "failed": failed}
    print(f"RAG 入库完成：新增 {created}，已存在 {duplicated}，跳过 {skipped}，失败 {failed}")
    print(json.dumps(summary, ensure_ascii=False))
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()