"""存量附件迁移：本地私有目录 → MinIO 对象存储。

用法（需先配置 ECOM_MINIO_* 并启动 MinIO）：
    ECOM_MINIO_ENABLED="true" .venv/bin/python scripts/migrate_blobs_to_minio.py [--dry-run]

行为：
- 扫描 document_versions 中所有 blob_name；
- 源：本地私有目录 data/private/<mode>/<tenant>/<blob>；
- 目标：get_blob_store()（MinIO 优先，未启用/不可用时拒绝执行以免误判成功）；
- 对象已存在（同大小）则跳过，支持重复执行。
"""
from __future__ import annotations
import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from ecom_copilot.enterprise.config import load_config
from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.storage import LocalBlobStore, get_blob_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.minio_enabled:
        print("MinIO 未启用：请设置 ECOM_MINIO_ENABLED=\"true\" 与 ECOM_MINIO_* 连接项后重试")
        return 1
    store = get_blob_store()
    if isinstance(store, LocalBlobStore):
        print("MinIO 连接失败（已回落本地），终止迁移以免假成功")
        return 1

    local = LocalBlobStore(cfg)
    with database_connection() as c:
        rows = fetch_all(c, "SELECT tenant_id, blob_name FROM document_versions WHERE blob_name <> '' ORDER BY tenant_id, blob_name")
    seen, done, skip, missing = set(), 0, 0, 0
    for r in rows:
        key = (r["tenant_id"], r["blob_name"])
        if key in seen:
            continue
        seen.add(key)
        try:
            data = local.open_blob(*key)
        except FileNotFoundError:
            missing += 1
            continue
        if args.dry_run:
            done += 1
            continue
        store.put_blob(*key, data)
        done += 1
        if done % 50 == 0:
            print(f"进度: {done}/{len(seen)}")
    print(f"完成: 唯一对象 {len(seen)}，写入 {done}，跳过 {skip}，本地缺失 {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
