"""原文中的商品提及关系抽取与检索；不推断未在原文出现的产品属性。"""
from __future__ import annotations

import hashlib
import re
from typing import List

from taozhi.infrastructure.persistence.access_repo import can_read
from ..knowledge.db import fetch_all

# 覆盖带连字符/下划线型号（LX-20、MC-500）及紧凑字母+数字型号（C1、C5、C1S）
_ENTITY_RE = re.compile(
    r"(?<![A-Za-z0-9.])[A-Za-z]{1,8}[-_]\d{1,6}[A-Za-z]?(?![A-Za-z0-9.])"
    r"|(?<![A-Za-z0-9.])[A-Z]{1,3}\d{1,4}[A-Z]?(?![A-Za-z0-9.])"
)
_IGNORED_PREFIXES = re.compile(r"^(?:V\d+|L[1-3]|H\d+|T\d+|PD\d+|QC\d+)$", re.IGNORECASE)


def extract_entities(text: str) -> List[str]:
    """从文本或提问中提取去重后的商品/型号标识，排除版本号与密级标记。"""
    if not text:
        return []
    seen = set()
    out: List[str] = []
    for raw in _ENTITY_RE.findall(text):
        token = raw.strip()
        if not token or _IGNORED_PREFIXES.match(token):
            continue
        key = token.upper()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def index_mentions(conn, tenant, doc_id, version, chunks):
    """抽取文档切片中的实体提及并幂等写入 knowledge_mentions。"""
    conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s AND version=%s", (doc_id, version))
    for chunk in chunks:
        for name in extract_entities(chunk["text"]):
            fid = "fact_" + hashlib.sha256((chunk["id"] + ":" + name).encode()).hexdigest()[:24]
            conn.execute(
                "INSERT INTO knowledge_mentions(id,tenant_id,document_id,version,chunk_id,entity) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (fid, tenant, doc_id, version, chunk["id"], name),
            )


def list_visible_mentions(conn, p):
    """列出当前用户有权访问且处于发布版本中的实体提及。"""
    docs = fetch_all(conn, "SELECT * FROM documents WHERE tenant_id=%s AND state='published'", (p.tenant_id,))
    allowed = {d["id"]: d for d in docs if can_read(conn, p, d)}
    if not allowed:
        return []
    rows = fetch_all(
        conn,
        "SELECT f.* FROM knowledge_mentions f "
        "JOIN documents d ON d.id=f.document_id AND d.current_version=f.version "
        "WHERE f.tenant_id=%s AND f.document_id=ANY(%s) LIMIT 1000",
        (p.tenant_id, list(allowed)),
    )
    return [
        dict(
            entity=r["entity"],
            relation="资料提及商品",
            document_id=r["document_id"],
            version=r["version"],
            title=allowed[r["document_id"]]["title"],
        )
        for r in rows
    ]
