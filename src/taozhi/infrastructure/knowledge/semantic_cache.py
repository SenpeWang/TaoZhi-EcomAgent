"""复用可访问的已核验文档答复，校验租户、密级、主体、业务条件和脱敏范围。"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np

from taozhi.config import Config, load_config
from taozhi.infrastructure.knowledge.embedding import available as embedding_available, encode_query
from taozhi.domain.entity_extraction import extract_entities
from taozhi.domain.privacy import redact
from taozhi.observability.tracing import record_diagnostic

logger = logging.getLogger("taozhi.server.semantic_cache")


@dataclass
class CacheEntry:
    """语义缓存条目。"""
    id: str
    embedding: np.ndarray
    answer: str
    citations: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    input_level: int
    created_at: float
    hits: int = 0
    redacted: bool = False
    entities: Tuple[str, ...] = field(default_factory=tuple)
    fact_question: bytes = b""


_BUSINESS_FACT = re.compile(
    r"本[店司款品]|我[司们店]|公司|内部|保修|质保|退换|七天|无理由|赔付|条款|政策|保价|退款|"
    r"报价|价格|定价|折扣|售价|底价|合同|供应商|采购|成本|毛利|利润|佣金|审批|制度|"
    r"库存|交期|账期|订单|运费|包邮|适配|兼容|型号|规格|切幅|尺寸|功率|支持|承诺|授权|"
    r"线上|线下|直营|经销|门店|平台|渠道|生效|今年|去年|明年|多久|几天|退货期|维修|"
    r"营业|上班|下班|值班|联系人|客服电话|供应链|售后|发票|赔偿|返利|[0-9]|"
    r"usb|type-c|蓝牙|bluetooth|gtin|sku|\bble\b", re.I,
)


def _fact_question(query: str) -> bytes:
    """涉及内部承诺或具体参数时，将缓存复用限定在同一问题。"""
    if not _BUSINESS_FACT.search(query):
        return b""
    normalized = unicodedata.normalize("NFKC", query).strip().rstrip("?。!").strip()
    return hashlib.sha256(normalized.encode()).digest()


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
        self._entries: Dict[str, List[CacheEntry]] = {}
        self._matrices: Dict[str, np.ndarray] = {}
        self._tenant_epochs: Dict[str, int] = {}

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
            self._matrices.pop(tenant_id, None)
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
            q_vec = encode_query(query)[0]
        except Exception as exc:
            record_diagnostic("retrieval.embedding", "skipped", exc)
            with self._lock:
                self._misses += 1
            return None

        with self._lock:
            self._sync_epoch_unlocked(tenant_id, epoch)
            entries = self._entries.get(tenant_id, [])
            matrix = self._matrices.get(tenant_id)
            if not entries or matrix is None or matrix.shape[1] != len(q_vec):
                self._misses += 1
                return None

            now = time.monotonic()
            q_entities = _norm_entities(query)
            fact_question = _fact_question(query)
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
                if entry.entities != q_entities or entry.fact_question != fact_question:
                    continue
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
                    "语义缓存命中（相似度 %.4f，耗时 %.2f 毫秒）",
                    best_sim,
                    elapsed,
                )
                matched_payload = {
                    "entry_id": entry.id,
                    "cache_hit": True,
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
        if not sources or any(s.get("kind") != "document" for s in sources):
            return False
        if self.max_entries_per_tenant <= 0 or not answer.strip() or not embedding_available():
            return False

        try:
            q_vec = encode_query(query)[0]
        except Exception as exc:
            record_diagnostic("retrieval.embedding", "skipped", exc)
            return False

        with self._lock:
            self._sync_epoch_unlocked(tenant_id, epoch)
            entries = self._entries.setdefault(tenant_id, [])
            if entries and len(entries[0].embedding) != len(q_vec):
                entries.clear()
            if len(entries) >= self.max_entries_per_tenant:
                entries.sort(key=lambda x: (x.hits, x.created_at))
                entries.pop(0)

            entry = CacheEntry(
                id=f"sc_{uuid4().hex}",
                embedding=q_vec,
                answer=answer,
                citations=citations,
                sources=sources,
                input_level=input_level,
                created_at=time.monotonic(),
                redacted=redacted,
                entities=_norm_entities(query),
                fact_question=_fact_question(query),
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
