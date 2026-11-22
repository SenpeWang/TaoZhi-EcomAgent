"""批量导入企业语料：按目录映射权限层级，解析、切片、向量化并写入索引。"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Iterable

from taozhi.config import load_config
from taozhi.domain.text_guard import strip_invalid_text
from taozhi.infrastructure.knowledge.embedding import (
    available as embed_available,
    encode_passages,
    embedding_input,
)
from taozhi.infrastructure.persistence.database import database_connection, fetch_all, fetch_one

ROOT = Path(__file__).resolve().parents[1]

MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

DEFAULT_POLICY = {"scope": "company", "level": 2, "ai_allowed": True}

POLICY_RULES: dict[str, dict[str, Any]] = {
    "data/accessory_pdfs": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/product": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/products_crawled": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/compatibility": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/tablets": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/chargers": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/protectors": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/audio": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/wearables": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/notices": {"scope": "company", "level": 1, "ai_allowed": True},
    "data/corpus/manuals": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/quality": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/after_sales": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/training": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/sop_policies": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/documents": {"scope": "company", "level": 2, "ai_allowed": True},
    "data/corpus/policies": {"scope": "company", "level": 3, "ai_allowed": True},
    "data/corpus/sku": {"scope": "company", "level": 3, "ai_allowed": True},
    "data/incoming": {"scope": "company", "level": 2, "ai_allowed": True},
}


def policy_for(path: Path) -> dict[str, Any]:
    """按相对目录匹配权限策略，未命中时使用默认策略。

    Args:
        path: 源文件绝对路径。

    Returns:
        含 scope、level、ai_allowed 的策略字典。
    """
    rel = path.resolve().relative_to(ROOT).as_posix()
    best = ""
    policy = dict(DEFAULT_POLICY)
    for prefix, rule in POLICY_RULES.items():
        if rel.startswith(prefix) and len(prefix) > len(best):
            best, policy = prefix, dict(rule)
    return policy


def read_pages(path: Path) -> tuple[str, list[tuple[int, str]]]:
    """读取单个文件的正文文本与分页内容。

    Args:
        path: 源文件路径。

    Returns:
        全文文本与分页列表；不支持的格式返回空内容。
    """
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix in (".md", ".txt"):
        text = data.decode("utf-8", errors="ignore")
        return text, [(0, text)]
    if suffix == ".pdf":
        from taozhi.infrastructure.knowledge.ingest import parse

        return parse(data, "application/pdf")
    if suffix == ".docx":
        from taozhi.infrastructure.knowledge.ingest import parse

        return parse(data, MIME_BY_SUFFIX[".docx"])
    return "", []


def title_of(path: Path, body: str) -> str:
    """提取文档标题：优先一级标题，其次文件主名。

    Args:
        path: 源文件路径。
        body: 已解析正文。

    Returns:
        不超过 120 字符的标题。
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:120]
    return path.stem.replace("_", " ")[:120]


def iter_sources(dirs: Iterable[Path]) -> Iterable[Path]:
    """展开待导入文件，跳过清单与说明文件。

    Args:
        dirs: 待扫描目录列表。

    Yields:
        可解析的源文件路径。
    """
    for base in dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in MIME_BY_SUFFIX:
                continue
            if path.name.lower() in {"readme.md", "manifest.json"}:
                continue
            yield path


def import_files(dirs: list[Path], tenant_id: str, dry_run: bool = False) -> dict[str, int]:
    """执行批量导入并返回统计。

    Args:
        dirs: 待导入目录。
        tenant_id: 目标租户编号。
        dry_run: 仅统计不写库。

    Returns:
        含扫描、导入、跳过、失败与切片数的统计字典。
    """
    from taozhi.infrastructure.knowledge import es_index
    from taozhi.infrastructure.knowledge.ingest import split
    from taozhi.infrastructure.persistence.document_repo import index_mentions

    stats = {"scanned": 0, "imported": 0, "skipped": 0, "failed": 0, "chunks": 0}
    if not dry_run and not embed_available():
        print("嵌入服务不可用，终止导入", file=sys.stderr)
        raise SystemExit(2)

    with database_connection() as conn:
        tenant_row = fetch_one(conn, "SELECT id FROM tenants WHERE code=%s", (tenant_id,))
        if tenant_row:
            tenant_id = tenant_row["id"]
        root_node = fetch_one(
            conn,
            "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind='company' AND active ORDER BY id LIMIT 1",
            (tenant_id,),
        )
        creator = fetch_one(
            conn,
            "SELECT id FROM users WHERE tenant_id=%s AND active AND system_admin ORDER BY username LIMIT 1",
            (tenant_id,),
        )
        if not root_node or not creator:
            print("缺少公司根节点或管理员账号，终止", file=sys.stderr)
            raise SystemExit(2)
        node_id, creator_id = root_node["id"], creator["id"]

        for path in iter_sources(dirs):
            stats["scanned"] += 1
            try:
                body, pages = read_pages(path)
                body = strip_invalid_text(body)
                pages = [(page, strip_invalid_text(text)) for page, text in pages]
                if not body.strip():
                    stats["skipped"] += 1
                    continue
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                doc_id = "IMP_" + digest[:16]
                existing = fetch_one(
                    conn,
                    "SELECT content_hash FROM document_versions WHERE document_id=%s AND version=1",
                    (doc_id,),
                )
                if existing and existing["content_hash"] == digest:
                    stats["skipped"] += 1
                    continue
                policy = policy_for(path)
                title = title_of(path, body)
                if dry_run:
                    stats["imported"] += 1
                    continue
                conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s", (doc_id,))
                conn.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
                conn.execute("DELETE FROM document_versions WHERE document_id=%s", (doc_id,))
                conn.execute("DELETE FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, doc_id))
                conn.execute(
                    "INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,state,current_version) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'published',1)",
                    (doc_id, tenant_id, node_id, creator_id, title, policy["scope"], policy["level"], policy["ai_allowed"]),
                )
                conn.execute(
                    "INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,mime_type,created_by) "
                    "VALUES(%s,%s,1,%s,%s,%s,%s,%s)",
                    (tenant_id, doc_id, title, body, digest, MIME_BY_SUFFIX[path.suffix.lower()], creator_id),
                )
                chunks = split(pages or [(0, body)], doc_id, 1)
                for chunk in chunks:
                    chunk["text"] = strip_invalid_text(chunk["text"])
                vectors = encode_passages([embedding_input(title, ch["text"]) for ch in chunks])
                for chunk, vector in zip(chunks, vectors):
                    conn.execute(
                        "INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text,embedding) "
                        "VALUES(%s,%s,%s,1,%s,%s,%s,%s)",
                        (chunk["id"], tenant_id, doc_id, chunk["ordinal"], chunk["page"], chunk["text"], vector),
                    )
                index_mentions(conn, tenant_id, doc_id, 1, chunks)
                stats["chunks"] += len(chunks)
                stats["imported"] += 1
                if es_index.available():
                    es_index.delete_chunks(doc_id, 1)
                    rows = fetch_all(
                        conn,
                        "SELECT c.id,c.tenant_id,c.document_id,c.version,c.ordinal,c.page,c.text,d.title "
                        "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.document_id=%s AND c.version=1",
                        (doc_id,),
                    )
                    es_index.index_chunks(rows)
            except Exception as exc:
                stats["failed"] += 1
                print(f"失败 {path.relative_to(ROOT)}: {type(exc).__name__} {exc}"[:200], file=sys.stderr)
    return stats


def main() -> int:
    """命令行入口：解析参数并执行导入。

    Returns:
        进程退出码，全部成功为 0。
    """
    parser = argparse.ArgumentParser(description="批量导入企业语料并建立向量索引")
    parser.add_argument("--dirs", nargs="*", default=["data/accessory_pdfs", "data/corpus", "data/incoming"])
    parser.add_argument("--tenant", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tenant = args.tenant or load_config().tenant_code
    dirs = [ROOT / item for item in args.dirs]
    stats = import_files(dirs, tenant, dry_run=args.dry_run)
    print(
        f"扫描 {stats['scanned']} / 导入 {stats['imported']} / 跳过 {stats['skipped']} / "
        f"失败 {stats['failed']} / 切片 {stats['chunks']}"
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())