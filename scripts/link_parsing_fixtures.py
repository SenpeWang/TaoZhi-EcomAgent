"""把评测解析夹具的 PDF/DOCX 原件回填为文档归档，供知识文档按原件格式查看。

解析夹具按 ``evals/dataset/spec/parsing.yaml`` 确定性渲染，导入时只保存了解析
正文；本脚本重新渲染同一份规格并写入对象存储，不重算切片与向量。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.dataset.parsing import build

from taozhi.infrastructure.knowledge.storage import get_blob_store
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.database import database_connection, fetch_one

SPEC = ROOT / "evals/dataset/spec/parsing.yaml"
MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def link_fixtures(apply: bool) -> dict[str, int]:
    """为缺少原件的解析夹具文档渲染并登记 PDF/DOCX 文件。

    Args:
        apply: 为 False 时只统计，不写对象存储与数据库。

    Returns:
        扫描、回填、跳过与未匹配的计数。
    """
    entries = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["documents"]
    stats = {"scanned": 0, "linked": 0, "skipped": 0, "missing": 0}
    store = get_blob_store() if apply else None
    with tempfile.TemporaryDirectory(prefix="parsing-fixtures-") as tmp:
        with database_connection() as conn:
            for entry in entries:
                stats["scanned"] += 1
                row = fetch_one(
                    conn,
                    "SELECT d.tenant_id, v.version, v.blob_name FROM documents d "
                    "JOIN document_versions v ON v.document_id=d.id AND v.version=d.current_version "
                    "WHERE d.id=%s AND d.state<>'deleted'",
                    (entry["doc_id"],),
                )
                if not row:
                    stats["missing"] += 1
                    continue
                if row["blob_name"]:
                    stats["skipped"] += 1
                    continue
                path, _text = build(entry, Path(tmp))
                if apply:
                    name = job_repo.generate_id("blob") + path.suffix
                    store.put_blob(row["tenant_id"], name, path.read_bytes())
                    conn.execute(
                        "UPDATE document_versions SET blob_name=%s, mime_type=%s "
                        "WHERE document_id=%s AND version=%s",
                        (name, MIME_BY_FORMAT[entry["format"]], entry["doc_id"], row["version"]),
                    )
                stats["linked"] += 1
    return stats


def main() -> int:
    """解析命令行并执行回填；默认预演，显式 --apply 才写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写入对象存储与数据库")
    args = parser.parse_args()
    stats = link_fixtures(args.apply)
    verb = "已回填" if args.apply else "预演"
    print(verb + "：" + "，".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
