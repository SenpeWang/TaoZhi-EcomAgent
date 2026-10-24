"""企业记忆系统与来源追踪。

结合数据库历史检索与 Antigravity (agy) 规范的长效记忆、双轨日志与上下文压实。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .db import fetch_all, fetch_one
from .policy import sources_valid
from .privacy import redact
from .subagent.memory.compactor import ContextCompactor
from .subagent.memory.store import LongTermMemoryStore
from .subagent.memory.transcript import TranscriptLogger, TranscriptStep

_global_store: Optional[LongTermMemoryStore] = None


def get_memory_store() -> LongTermMemoryStore:
    """获取全局共享的长效记忆库单例。

    Returns:
        LongTermMemoryStore: 实例对象。
    """
    global _global_store
    if _global_store is None:
        _global_store = LongTermMemoryStore()
    return _global_store


def get_followup_context(
    conn: Any,
    p: Any,
    question: str,
    input_level: int,
    conversation_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """同一会话内直接继承上文证据；无会话标识时退回关键词启发式（历史行为）。

    Args:
        conn: 数据库连接。
        p: 主体权限对象。
        question: 用户追问内容。
        input_level: 安全级别。
        conversation_id: 会话标识。

    Returns:
        Tuple[List[Dict[str, Any]], List[Any]]: 证据片段列表及来源标识列表。
    """
    if conversation_id:
        rows = fetch_all(
            conn,
            "SELECT result,sources,input_level FROM jobs WHERE tenant_id=%s AND owner_id=%s AND conversation_id=%s AND kind='ask' AND state='completed' ORDER BY created_at DESC LIMIT 3",
            (p.tenant_id, p.id, conversation_id),
        )
    else:
        if not re.search(r"刚才|上面|这款|那个|它的|继续|价格呢|还有呢", question):
            return [], []
        rows = fetch_all(
            conn,
            "SELECT result,sources,input_level FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='ask' AND state='completed' ORDER BY created_at DESC LIMIT 3",
            (p.tenant_id, p.id),
        )

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
    "TranscriptLogger",
    "TranscriptStep",
    "ContextCompactor",
]
