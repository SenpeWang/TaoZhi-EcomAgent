"""迁移期附件比对：本地私有目录 ↔ MinIO 对象存储。

用法（需先配置 ECOM_MINIO_* 并启动 MinIO）：
    ECOM_MINIO_ENABLED="true" .venv/bin/python scripts/verify_blob_migration.py [--json]

行为：
- 扫描 document_versions 中所有 blob_name，逐个比对两侧大小与 sha256；
- 输出内容不一致、本地缺失、远端缺失三类清单；
- 作为双写迁移的切换判据：全部一致（退出码 0）后，方可关闭 ECOM_BLOB_DUAL_WRITE。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from ecom_copilot.enterprise.config import load_config
from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.storage import LocalBlobStore, MinioBlobStore


def _digest(data: bytes) -> str:
    """返回字节内容的 sha256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="以 JSON 输出比对结果")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.minio_enabled:
        print("MinIO 未启用：请设置 ECOM_MINIO_ENABLED=\"true\" 与 ECOM_MINIO_* 连接项后重试")
        return 1
    local = LocalBlobStore(cfg)
    remote = MinioBlobStore(cfg)
    try:
        remote.client.bucket_exists(cfg.minio_bucket)  # 连通性探测
    except Exception as exc:
        print("MinIO 无法连接，终止比对:", type(exc).__name__)
        return 1

    with database_connection() as c:
        rows = fetch_all(c, "SELECT tenant_id, blob_name FROM document_versions WHERE blob_name <> '' ORDER BY tenant_id, blob_name")
    seen, matched = set(), 0
    mismatched, local_missing, remote_missing = [], [], []
    for r in rows:
        key = (r["tenant_id"], r["blob_name"])
        if key in seen:
            continue
        seen.add(key)
        try:
            ldata = local.open_blob(*key)
        except FileNotFoundError:
            local_missing.append(list(key))
            continue
        try:
            rdata = remote.open_blob(*key)
        except Exception:
            remote_missing.append(list(key))
            continue
        if len(ldata) == len(rdata) and _digest(ldata) == _digest(rdata):
            matched += 1
        else:
            mismatched.append({"key": list(key), "local_size": len(ldata), "remote_size": len(rdata),
                               "local_sha256": _digest(ldata), "remote_sha256": _digest(rdata)})
    report = {"total": len(seen), "matched": matched, "mismatched": mismatched,
              "local_missing": local_missing, "remote_missing": remote_missing}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"比对完成: 唯一对象 {len(seen)}，一致 {matched}，内容不一致 {len(mismatched)}，本地缺失 {len(local_missing)}，远端缺失 {len(remote_missing)}")
        for item in mismatched[:20]:
            print("  不一致:", item["key"])
        for key in local_missing[:20]:
            print("  本地缺失:", key)
        for key in remote_missing[:20]:
            print("  远端缺失:", key)
    return 0 if not (mismatched or local_missing or remote_missing) else 1


if __name__ == "__main__":
    raise SystemExit(main())