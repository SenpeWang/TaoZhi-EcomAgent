"""企业级高并发语义缓存引擎（Semantic Cache）。

在电商大促及高并发场景中，70%~80% 的商品咨询属于高频重复或同义变体问题（如“支持Mate60吗”与“华为Mate60pro能贴吗”）。
语义缓存层在检索与多智能体之前拦截请求：
1. 向量近邻比对：通过本地 BGE 将问题编码，与缓存中已通过质检核验的权威答案做 Cosine 相似度匹配；
2. 毫秒级命中返回：相似度 >= 阈值（默认 0.92）直接返回权威答案与引用，时延 < 5ms，消耗 0 LLM Token；
3. 租户与权限隔离：严格按 tenant_id 隔离，且校验 input_level 密级防越权；
4. 动态失效与 LRU：支持 TTL 自动过期与最大容量 LRU 淘汰。

密级与脱敏是两件事，必须各判各的：缓存里存的是"当初那位提问者"看到的文本，
boss 看到的是未脱敏原文（手机号、身份证、收货地址）。按密级放行会让 staff 用
一个相似问题拿到 boss 那份原文，所以命中时还要做一次脱敏对齐（见 get()）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import Config, load_config
from ..knowledge.embedding import available as embedding_available, encode_query
from taozhi.domain.entity_extraction import extract_entities
from ..knowledge.retriever import SEMANTIC_DIM
from taozhi.domain.privacy import redact

logger = logging.getLogger("taozhi.server.semantic_cache")


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
    # 存进去的 answer / citations 是否已脱敏。False 表示当初是 boss 问的、
    # 内容是未脱敏原文；命中者不是 boss 时必须重新脱敏后再返回。
    redacted: bool = False
    entities: Tuple[str, ...] = field(default_factory=tuple)


def _norm_entities(query: str) -> Tuple[str, ...]:
    """提取查询中的规范化型号/实体集合，阻止仅型号不同的问题跨命中缓存。"""
    return tuple(sorted({e.upper() for e in extract_entities(query)}))


class SemanticCache:
    """高性能线程安全语义缓存。"""

    def __init__(
        self,
        similarity_threshold: Optional[float] = None,
        ttl_seconds: Optional[int] = None,
        max_entries_per_tenant: Optional[int] = None,
    ) -> None:
        try:
            cfg = load_config()
        except Exception:
            cfg = None
        self.similarity_threshold = float(
            similarity_threshold
            if similarity_threshold is not None
            else getattr(cfg, "semantic_cache_threshold", Config.semantic_cache_threshold)
        )
        self.ttl_seconds = int(
            ttl_seconds if ttl_seconds is not None else getattr(cfg, "semantic_cache_ttl", Config.semantic_cache_ttl)
        )
        self.max_entries_per_tenant = int(
            max_entries_per_tenant
            if max_entries_per_tenant is not None
            else getattr(cfg, "semantic_cache_max_entries", Config.semantic_cache_max_entries)
        )

        self._lock = threading.Lock()
        # {tenant_id: [CacheEntry, ...]}
        self._entries: Dict[str, List[CacheEntry]] = {}
        # {tenant_id: np.ndarray(N, 512)}
        self._matrices: Dict[str, np.ndarray] = {}
        # {tenant_id: int} 跨进程租户权限/资料代际水位（对齐 tenants.epoch）
        self._tenant_epochs: Dict[str, int] = {}

        # 监控统计
        self._hits = 0
        self._misses = 0

    def _sync_epoch_unlocked(self, tenant_id: str, epoch: Optional[int]) -> None:
        """在持有锁的前提下比对租户代际版本；若发生跨进程撤权或删改则清空旧缓存。"""
        if epoch is None:
            return
        prev = self._tenant_epochs.get(tenant_id)
        if prev is not None and prev != int(epoch):
            self._entries.pop(tenant_id, None)
            self._matrices.pop(tenant_id, None)
        self._tenant_epochs[tenant_id] = int(epoch)

    def _rebuild_matrix(self, tenant_id: str) -> None:
        """重构租户向量矩阵，供矩阵点积批量加速。"""
        entries = self._entries.get(tenant_id, [])
        if not entries:
            self._matrices[tenant_id] = np.zeros((0, SEMANTIC_DIM), dtype="float32")
        else:
            self._matrices[tenant_id] = np.vstack([e.embedding for e in entries])

    def get(
        self,
        tenant_id: str,
        query: str,
        input_level: int = 1,
        boss: bool = False,
        epoch: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """检索语义缓存。

        Args:
            tenant_id: 租户 ID。
            query: 用户提问。
            input_level: 当前用户密级上限（缓存内容不能高于此密级）。
            boss: 命中者是否老板。决定要不要对未脱敏的缓存内容重新脱敏；
                默认 False，即缺省按最严格口径处理。
            epoch: 可选租户权限代际号（``tenants.epoch``），跨进程变化时自动淘汰旧条目。

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
            self._sync_epoch_unlocked(tenant_id, epoch)
            entries = self._entries.get(tenant_id, [])
            matrix = self._matrices.get(tenant_id)
            if not entries or matrix is None or len(matrix) == 0:
                self._misses += 1
                return None

            now = time.monotonic()
            q_entities = _norm_entities(query)
            # 批量余弦相似度计算（向量均已 L2 归一化，点积即余弦相似度）
            sims = np.dot(matrix, q_vec)
            candidates = [int(i) for i in np.argsort(-sims) if float(sims[i]) >= self.similarity_threshold]

            expired_indices = []
            matched_payload: Optional[Dict[str, Any]] = None
            for idx in candidates:
                entry = entries[idx]
                if now - entry.created_at > self.ttl_seconds:
                    expired_indices.append(idx)
                    continue
                if entry.input_level > input_level:
                    continue
                if entry.entities != q_entities:
                    continue
                # 脱敏对齐：boss 命中 staff 已脱敏残缺条目时继续查找更合适候选或回落完整链路
                if boss and entry.redacted:
                    continue
                answer = entry.answer
                citations = entry.citations
                if not boss and not entry.redacted:
                    answer = redact(answer)
                    citations = [
                        dict(c, quote=redact(c["quote"])) if c.get("quote") else c
                        for c in citations
                    ]
                entry.hits += 1
                self._hits += 1
                elapsed = (time.monotonic() - t0) * 1000
                best_sim = float(sims[idx])
                logger.info(
                    "语义缓存命中! 相似度: %.4f, 匹配原问题: '%s', 耗时: %.2fms",
                    best_sim,
                    entry.query,
                    elapsed,
                )
                matched_payload = {
                    "entry_id": entry.id,
                    "cache_hit": True,
                    "matched_query": entry.query,
                    "similarity": round(best_sim, 4),
                    "answer": answer,
                    "citations": citations,
                    "sources": entry.sources,
                    "latency_ms": round(elapsed, 2),
                }
                break

            if expired_indices:
                for idx in sorted(expired_indices, reverse=True):
                    entries.pop(idx)
                self._rebuild_matrix(tenant_id)

            if matched_payload is not None:
                return matched_payload

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
        redacted: bool = False,
        epoch: Optional[int] = None,
    ) -> bool:
        """将高质量权威答案与证据写入语义缓存。

        redacted 记的是这批内容有没有脱敏过（boss 的答案没有），命中时据此对齐；
        可选传入 epoch（``tenants.epoch``）以支持 API↔Worker 跨进程权限失效感知。
        """
        if not embedding_available() or not answer.strip():
            return False

        try:
            q_vec = encode_query(query)[0]
        except Exception:
            return False

        with self._lock:
            self._sync_epoch_unlocked(tenant_id, epoch)
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
                redacted=redacted,
                entities=_norm_entities(query),
            )
            entries.append(entry)
            self._rebuild_matrix(tenant_id)
            return True

    def invalidate(
        self,
        tenant_id: str,
        entry_id: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """按条目 ID 或来源文档 ID 精准淘汰失效缓存，避免阻塞后续合规条目。"""
        with self._lock:
            entries = self._entries.get(tenant_id)
            if not entries:
                return 0
            before = len(entries)
            kept = []
            for entry in entries:
                if entry_id and entry.id == entry_id:
                    continue
                if document_id and any(s.get("document_id") == document_id for s in (entry.sources or [])):
                    continue
                kept.append(entry)
            removed = before - len(kept)
            if removed:
                self._entries[tenant_id] = kept
                self._rebuild_matrix(tenant_id)
            return removed

    def clear(self, tenant_id: Optional[str] = None) -> None:
        """清空缓存（如知识库大版本变更时调用）。"""
        with self._lock:
            if tenant_id:
                self._entries.pop(tenant_id, None)
                self._matrices.pop(tenant_id, None)
                self._tenant_epochs.pop(tenant_id, None)
            else:
                self._entries.clear()
                self._matrices.clear()
                self._tenant_epochs.clear()

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
