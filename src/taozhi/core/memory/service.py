"""企业记忆系统与来源追踪。

结合数据库历史检索与自研长效记忆存储管理租户隔离记忆。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Tuple

from ...knowledge.db import fetch_all, fetch_one
from taozhi.infrastructure.persistence.access_repo import sources_valid
from taozhi.domain.privacy import redact
from taozhi.domain.question_context import recalls_previous_evidence
from .store import LongTermMemoryStore

_STORES_LOCK = threading.Lock()
_stores: Dict[str, LongTermMemoryStore] = {}


def get_memory_store(tenant_id: str = "default") -> LongTermMemoryStore:
    """获取指定租户的长效记忆库实例（线程安全懒加载与物理隔离）。

    Args:
        tenant_id: 租户标识符。

    Returns:
        LongTermMemoryStore: 租户专属长效记忆库实例。
    """
    tid = str(tenant_id).strip() or "default"
    with _STORES_LOCK:
        if tid not in _stores:
            _stores[tid] = LongTermMemoryStore(tenant_id=tid)
        return _stores[tid]


def get_followup_context(
    conn: Any,
    p: Any,
    question: str,
    input_level: int,
    conversation_id: str = "",
    *,
    before: tuple[datetime, str] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """同一会话内直接继承上文证据；无会话标识时退回关键词启发式（历史行为）。

    Args:
        conn: 数据库连接。
        p: 主体权限对象。
        question: 用户追问内容。
        input_level: 安全级别。
        conversation_id: 会话标识。
        before: 当前提问的创建时间和任务编号，用于限制历史范围。

    Returns:
        Tuple[List[Dict[str, Any]], List[Any]]: 证据片段列表及来源标识列表。
    """
    if not recalls_previous_evidence(question, conversation_id):
        return [], []
    filters = ["tenant_id=%s", "owner_id=%s", "kind='ask'", "state='completed'"]
    params = [p.tenant_id, p.id]
    if conversation_id:
        filters.append("conversation_id=%s")
        params.append(conversation_id)
    if before is not None:
        filters.append("(created_at,id)<(%s,%s)")
        params.extend(before)
    rows = fetch_all(conn, "SELECT result,sources,input_level FROM jobs WHERE " + " AND ".join(filters)
                     + " ORDER BY created_at DESC,id DESC LIMIT 3", tuple(params))

    for past in rows:
        if not past["result"] or past["input_level"] > input_level or not sources_valid(conn, p, past["sources"]):
            continue
        evidence = []
        for citation in past["result"].get("citations", [])[:5]:
            chunk = fetch_one(
                conn,
                "SELECT c.*,d.title,d.node_id,d.level,d.ai_allowed,d.acl_version FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.tenant_id=%s AND c.id=%s AND d.current_version=c.version AND d.state='published'",
                (p.tenant_id, citation["chunk_id"]),
            )
            if not chunk:
                continue
            evidence.append(
                dict(
                    document_id=chunk["document_id"],
                    version=chunk["version"],
                    acl_version=chunk["acl_version"],
                    chunk_id=chunk["id"],
                    title=chunk["title"],
                    quote=chunk["text"] if p.boss else redact(chunk["text"]),
                    page=chunk["page"],
                    level=chunk["level"],
                    node_id=chunk["node_id"],
                    tenant_id=p.tenant_id,
                    ai_allowed=chunk["ai_allowed"],
                    score=1.0,
                    kind="document",
                )
            )
        if evidence:
            return evidence, past["sources"]
    return [], []


__all__ = [
    "get_followup_context",
    "get_memory_store",
    "LongTermMemoryStore",
]
