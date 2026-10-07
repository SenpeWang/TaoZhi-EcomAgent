"""RRF（Reciprocal Rank Fusion）融合排序 + 权限过滤 + 轻量 Rerank。"""

from __future__ import annotations

import re
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import Settings, get_settings
from ..schemas.common import PermissionContext
from .channels import RetrievalHit


def reciprocal_rank_fusion(ranked_lists: Sequence[Sequence[RetrievalHit]],
                           k: int = 60, weights: Optional[Dict[str, float]] = None) -> List[RetrievalHit]:
    """RRF：score = Σ w_channel / (k + rank)。"""
    fused: Dict[str, float] = {}
    meta: Dict[str, RetrievalHit] = {}
    channels: Dict[str, set] = {}
    for hits in ranked_lists:
        for hit in hits:
            weight = (weights or {}).get(hit.channel, 1.0)
            fused[hit.chunk.id] = fused.get(hit.chunk.id, 0.0) + weight / (k + hit.rank)
            meta.setdefault(hit.chunk.id, hit)
            channels.setdefault(hit.chunk.id, set()).add(hit.channel)
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    results: List[RetrievalHit] = []
    for chunk_id, score in ordered:
        base = meta[chunk_id]
        merged = RetrievalHit(base.chunk, score, "+".join(sorted(channels[chunk_id])), 0)
        results.append(merged)
    for index, hit in enumerate(results, start=1):
        hit.rank = index
    return results


_STOPWORDS = {"的", "了", "和", "与", "及", "在", "对", "是", "如何", "什么", "哪些", "请", "分析"}


def lexical_overlap(query: str, text: str) -> float:
    terms = [t for t in re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z]{3,}", query)
             if t not in _STOPWORDS]
    if not terms:
        return 0.0
    hits = sum(1 for t in terms if t in text)
    return hits / len(terms)


def rerank(query: str, hits: Sequence[RetrievalHit], top_k: int,
           settings: Optional[Settings] = None) -> List[RetrievalHit]:
    """轻量 Rerank：融合分 × 词面覆盖 × 文档类型权重（避免额外 LLM 开销）。"""
    cfg = settings or get_settings()
    if not cfg.rerank_enabled or not hits:
        return list(hits)[:top_k]

    type_weight = {
        "spec_sheet": 1.1, "manual": 1.1, "product_page": 1.05, "faq": 1.05,
        "tutorial": 1.0, "policy": 0.9, "other": 1.0,
    }
    scored: List[Tuple[float, RetrievalHit]] = []
    max_score = max(h.score for h in hits) or 1.0
    for hit in hits:
        overlap = lexical_overlap(query, hit.chunk.text)
        norm = hit.score / max_score
        weight = type_weight.get(
            hit.chunk.doc_type.value if hasattr(hit.chunk.doc_type, "value") else str(hit.chunk.doc_type),
            1.0,
        )
        channel_bonus = 1.15 if "graph" in hit.channel else 1.0
        # 词面覆盖为主导，融合分为辅，避免高分但无关片段霸榜
        scored.append(((overlap + 0.35 * norm) * weight * channel_bonus, hit))
    scored.sort(key=lambda kv: kv[0], reverse=True)
    out = []
    for index, (score, hit) in enumerate(scored[:top_k], start=1):
        out.append(RetrievalHit(hit.chunk, score, hit.channel, index))
    return out


class QueryCache:
    """5 分钟查询缓存（PRD N4 缓存层）。"""

    def __init__(self, ttl: int = 300) -> None:
        self.ttl = ttl
        self._data: Dict[str, Tuple[float, List[RetrievalHit]]] = {}
        self._lock = threading.RLock()

    def _key(self, query: str, permission: Optional[PermissionContext], top_k: int) -> str:
        import json
        from ..security.access import get_access_store
        from ..security.accounts import get_account_store
        account=get_account_store().by_id(permission.user_id) if permission and permission.user_id.startswith("user_") else None
        version=account["version"] if account else 0
        ctx = json.dumps([permission.user_id,permission.org_tag,permission.is_admin,sorted(permission.roles),version,get_access_store().epoch()]) if permission else "anon"
        return f"{ctx}:{top_k}:{query}"

    def get(self, query: str, permission: Optional[PermissionContext], top_k: int):
        key = self._key(query, permission, top_k)
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            ts, hits = item
            if time.time() - ts > self.ttl:
                self._data.pop(key, None)
                return None
            return hits

    def put(self, query: str, permission: Optional[PermissionContext], top_k: int,
            hits: List[RetrievalHit]) -> None:
        with self._lock:
            now=time.time()
            for key in list(self._data):
                if now-self._data[key][0] > self.ttl:
                    self._data.pop(key,None)
            if len(self._data)>=1000:
                self._data.pop(next(iter(self._data)))
            self._data[self._key(query, permission, top_k)] = (time.time(), hits)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
