"""按内容哈希把语料源文件回填为文档归档原件，供前端按原始格式展示。

导入脚本以「解析正文的 sha256」生成文档编号，因此重新解析源文件即可精确
反查文档，无需按标题猜测。回填只写对象存储与 ``document_versions.blob_name``，
不重新切片、不改动向量与权限。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from import_corpus import MIME_BY_SUFFIX, iter_sources, read_pages

from taozhi.domain.text_guard import strip_invalid_text
from taozhi.infrastructure.knowledge.storage import get_blob_store
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.database import database_connection, fetch_one

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = ("data/accessory_pdfs", "data/corpus", "data/incoming")


def document_id(path: Path) -> tuple[str, bytes]:
    """重新解析源文件并返回与导入脚本一致的文档编号及原件字节。

    Args:
        path: 语料源文件路径。

    Returns:
        ``(document_id, data)``；正文为空时返回空编号。
    """
    body, _pages = read_pages(path)
    body = strip_invalid_text(body)
    if not body.strip():
        return "", b""
    return "IMP_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16], path.read_bytes()


def link_sources(dirs: list[Path], apply: bool) -> dict[str, int]:
    """为缺少归档原件的文档写入对象存储并登记 blob_name。

    Args:
        dirs: 待扫描的语料目录。
        apply: 为 False 时只统计，不写对象存储与数据库。

    Returns:
        扫描、回填、跳过与未匹配的计数。
    """
    stats = {"scanned": 0, "linked": 0, "skipped": 0, "missing": 0}
    store = get_blob_store() if apply else None
    with database_connection() as conn:
        for path in iter_sources(dirs):
            stats["scanned"] += 1
            doc_id, data = document_id(path)
            if not doc_id:
                stats["skipped"] += 1
                continue
            row = fetch_one(
                conn,
                "SELECT d.tenant_id, v.version, v.blob_name FROM documents d "
                "JOIN document_versions v ON v.document_id=d.id AND v.version=d.current_version "
                "WHERE d.id=%s AND d.state<>'deleted'",
                (doc_id,),
            )
            if not row:
                stats["missing"] += 1
                continue
            if row["blob_name"]:
                stats["skipped"] += 1
                continue
            if apply:
                mime = MIME_BY_SUFFIX[path.suffix.lower()]
                name = job_repo.generate_id("blob") + path.suffix.lower()
                store.put_blob(row["tenant_id"], name, data)
                conn.execute(
                    "UPDATE document_versions SET blob_name=%s, mime_type=%s "
                    "WHERE document_id=%s AND version=%s",
                    (name, mime, doc_id, row["version"]),
                )
            stats["linked"] += 1
    return stats


def main() -> int:
    """解析命令行并执行回填；默认预演，显式 --apply 才写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", nargs="*", default=list(DEFAULT_DIRS), help="语料目录，默认全部导入目录")
    parser.add_argument("--apply", action="store_true", help="实际写入对象存储与数据库")
    args = parser.parse_args()
    stats = link_sources([ROOT / item for item in args.dirs], args.apply)
    verb = "已回填" if args.apply else "预演"
    print(verb + "：" + "，".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
