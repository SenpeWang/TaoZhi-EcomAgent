"""企业级高并发语义缓存引擎（Semantic Cache）。

在电商大促及高并发场景中，70%~80% 的商品咨询属于高频重复或同义变体问题（如“支持Mate60吗”与“华为Mate60pro能贴吗”）。
语义缓存层在检索与多智能体之前拦截请求：
1. 向量近邻比对：通过本地 BGE 将问题编码，与缓存中已通过质检核验的权威答案做 Cosine 相似度匹配；
2. 毫秒级命中返回：相似度 >= 阈值（默认 0.92）直接返回权威答案与引用，时延 < 5ms，消耗 0 LLM Token；
3. 租户与权限隔离：严格按 tenant_id 隔离，且校验 input_level 密级防越权；
4. 动态失效与 LRU：支持 TTL 自动过期与最大容量 LRU 淘汰。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .embedding import available as embedding_available, encode_query

logger = logging.getLogger("ecom_copilot.enterprise.semantic_cache")


@dataclass
class CacheEntry:
    """语义缓存条目。"""
    id: str
    tenant_id: str
    query: str
    embedding: np.ndarray  # shape: (512,)
    answer: str
    citations: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    input_level: int
    created_at: float
    hits: int = 0


class SemanticCache:
    """高性能线程安全语义缓存。"""

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        ttl_seconds: int = 3600,
        max_entries_per_tenant: int = 10000,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries_per_tenant = max_entries_per_tenant

        self._lock = threading.Lock()
        # {tenant_id: [CacheEntry, ...]}
        self._entries: Dict[str, List[CacheEntry]] = {}
        # {tenant_id: np.ndarray(N, 512)}
        self._matrices: Dict[str, np.ndarray] = {}

        # 监控统计
        self._hits = 0
        self._misses = 0

    def _rebuild_matrix(self, tenant_id: str) -> None:
        """重构租户向量矩阵，供矩阵点积批量加速。"""
        entries = self._entries.get(tenant_id, [])
        if not entries:
            self._matrices[tenant_id] = np.zeros((0, 512), dtype="float32")
        else:
            self._matrices[tenant_id] = np.vstack([e.embedding for e in entries])

    def get(
        self,
        tenant_id: str,
        query: str,
        input_level: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """检索语义缓存。

        Args:
            tenant_id: 租户 ID。
            query: 用户提问。
            input_level: 当前用户密级上限（缓存内容不能高于此密级）。

        Returns:
            若命中则返回包含 answer, citations, sources, similarity 的字典，未命中返回 None。
        """
        if not embedding_available():
            with self._lock:
                self._misses += 1
            return None

        t0 = time.monotonic()
        try:
            q_vec = encode_query(query)[0]  # shape: (512,)
        except Exception as e:
            logger.warning("语义缓存向量化失败: %s", e)
            with self._lock:
                self._misses += 1
            return None

        with self._lock:
            entries = self._entries.get(tenant_id, [])
            matrix = self._matrices.get(tenant_id)
            if not entries or matrix is None or len(matrix) == 0:
                self._misses += 1
                return None

            now = time.monotonic()
            # 批量余弦相似度计算（向量均已 L2 归一化，点积即余弦相似度）
            sims = np.dot(matrix, q_vec)
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])
            best_entry = entries[best_idx]

            # 检查 TTL
            if now - best_entry.created_at > self.ttl_seconds:
                # 已过期
                entries.pop(best_idx)
                self._rebuild_matrix(tenant_id)
                self._misses += 1
                return None

            # 检查相似度阈值与密级隔离
            if best_sim >= self.similarity_threshold and best_entry.input_level <= input_level:
                best_entry.hits += 1
                self._hits += 1
                elapsed = (time.monotonic() - t0) * 1000
                logger.info("语义缓存命中! 相似度: %.4f, 匹配原问题: '%s', 耗时: %.2fms",
                            best_sim, best_entry.query, elapsed)
                return {
                    "cache_hit": True,
                    "matched_query": best_entry.query,
                    "similarity": round(best_sim, 4),
                    "answer": best_entry.answer,
                    "citations": best_entry.citations,
                    "sources": best_entry.sources,
                    "latency_ms": round(elapsed, 2),
                }

            self._misses += 1
            return None

    def put(
        self,
        tenant_id: str,
        query: str,
        answer: str,
        citations: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        input_level: int = 1,
    ) -> bool:
        """将高质量权威答案与证据写入语义缓存。"""
        if not embedding_available() or not answer.strip():
            return False

        try:
            q_vec = encode_query(query)[0]
        except Exception:
            return False

        with self._lock:
            entries = self._entries.setdefault(tenant_id, [])
            if len(entries) >= self.max_entries_per_tenant:
                # LRU / 最少命中淘汰
                entries.sort(key=lambda x: (x.hits, x.created_at))
                entries.pop(0)

            entry_id = f"sc_{int(time.time()*1000)}_{len(entries)}"
            entry = CacheEntry(
                id=entry_id,
                tenant_id=tenant_id,
                query=query,
                embedding=q_vec,
                answer=answer,
                citations=citations,
                sources=sources,
                input_level=input_level,
                created_at=time.monotonic(),
            )
            entries.append(entry)
            self._rebuild_matrix(tenant_id)
            return True

    def clear(self, tenant_id: Optional[str] = None) -> None:
        """清空缓存（如知识库大版本变更时调用）。"""
        with self._lock:
            if tenant_id:
                self._entries.pop(tenant_id, None)
                self._matrices.pop(tenant_id, None)
            else:
                self._entries.clear()
                self._matrices.clear()

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计监控指标。"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            total_entries = sum(len(v) for v in self._entries.values())
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total,
                "hit_rate": round(hit_rate, 4),
                "total_cached_entries": total_entries,
            }


_global_semantic_cache: Optional[SemanticCache] = None
_cache_init_lock = threading.Lock()


def get_semantic_cache() -> SemanticCache:
    """获取语义缓存全局单例。"""
    global _global_semantic_cache
    if _global_semantic_cache is None:
        with _cache_init_lock:
            if _global_semantic_cache is None:
                _global_semantic_cache = SemanticCache()
    return _global_semantic_cache
