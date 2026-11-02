"""独立语义缓存服务：位于接入层与编排层之间，拦截高频近邻重复意图。"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ...observability.tracer import get_current_trace


class CacheEntry(BaseModel):
    """缓存条目结构。"""
    query: str
    vector: Optional[List[float]] = None
    tenant_id: str
    security_level: int
    answer: str
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    hit_count: int = 0


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a, b in zip(v1, v2)))
    norm2 = math.sqrt(sum(b * b for a, b in zip(v1, v2)))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class SemanticCache:
    """语义缓存管理器。保证租户与密级前置隔离，支持高精余弦近邻检索。"""

    def __init__(self, similarity_threshold: float = 0.95, max_entries: int = 1000) -> None:
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self._entries: List[CacheEntry] = []

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        try:
            from ...knowledge.embedding import available, encode_query
            if available():
                vec = encode_query(text)
                return [float(x) for x in vec.reshape(-1)]
        except Exception:
            pass
        return None

    def get(
        self, query: str, tenant_id: str, security_level: int = 0
    ) -> Optional[Dict[str, Any]]:
        """查询语义缓存。严格校验租户隔离与密级权限。"""
        clean_q = query.strip().lower()
        if not clean_q:
            return None

        trace = get_current_trace()
        span_ctx = trace.span(
            "cache.semantic.lookup",
            kind="cache",
            input_data={"query": query, "tenant_id": tenant_id, "security_level": security_level},
        ) if trace else None

        query_vec = self._get_embedding(clean_q)
        hit_entry: Optional[CacheEntry] = None

        for entry in self._entries:
            # 严格租户隔离
            if entry.tenant_id != tenant_id:
                continue
            # 密级不可逾越：缓存内容的密级必须 <= 当前查询者的密级
            if entry.security_level > security_level:
                continue

            # 1. 字符串完全匹配直达
            if entry.query.strip().lower() == clean_q:
                hit_entry = entry
                break

            # 2. 向量语义近邻匹配
            if query_vec and entry.vector:
                sim = _cosine_similarity(query_vec, entry.vector)
                if sim >= self.similarity_threshold:
                    hit_entry = entry
                    break

        if span_ctx:
            with span_ctx as s:
                s.output_data = {"hit": hit_entry is not None}

        if hit_entry:
            hit_entry.hit_count += 1
            return {
                "answer": hit_entry.answer,
                "citations": hit_entry.citations,
                "cached": True,
            }
        return None

    def set(
        self,
        query: str,
        tenant_id: str,
        security_level: int,
        answer: str,
        citations: List[Dict[str, Any]],
        vector: Optional[List[float]] = None,
    ) -> None:
        """异步写入语义缓存。"""
        clean_q = query.strip()
        if not clean_q or not answer:
            return

        vec = vector or self._get_embedding(clean_q)
        if len(self._entries) >= self.max_entries:
            # 简单 LRU：按命中次数排序剔除
            self._entries.sort(key=lambda x: x.hit_count)
            self._entries.pop(0)

        entry = CacheEntry(
            query=clean_q,
            vector=vec,
            tenant_id=tenant_id,
            security_level=security_level,
            answer=answer,
            citations=citations,
        )
        self._entries.append(entry)


# 默认全局单例
default_semantic_cache = SemanticCache()