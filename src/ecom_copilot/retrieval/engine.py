"""RAG 检索层统一入口：四路召回 → RRF 融合 → 权限过滤 → Rerank → 缓存。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Dict, List, Optional, Sequence

from ..config import Settings, get_settings
from ..graph.store import GraphStore, get_graph_store
from ..observability import timer
from ..parsing.chunker import chunk_documents
from ..schemas.common import Evidence, PermissionContext, SourceDocument, TextChunk
from ..schemas.graph import GraphQueryResult
from .channels import BaseChannel, RetrievalHit, build_channels
from .fusion import QueryCache, reciprocal_rank_fusion, rerank
from .index import KnowledgeIndex

_CHANNEL_WEIGHTS = {"dense": 1.1, "bm25": 1.0, "hyde": 0.8, "graph": 1.2}


class RetrievalEngine:
    def __init__(self, settings: Optional[Settings] = None,
                 graph_store: Optional[GraphStore] = None) -> None:
        self.settings = settings or get_settings()
        self._lock = RLock()
        self.index = KnowledgeIndex(self.settings)
        self.graph_store = graph_store or get_graph_store(self.settings)
        self.channels: List[BaseChannel] = build_channels(
            self.index, self.settings, self.graph_store
        )
        self.cache = QueryCache(self.settings.cache_ttl_seconds)

    # ───────── 入库 ─────────
    def index_documents(self, docs: Sequence[SourceDocument]) -> int:
        chunks = chunk_documents(list(docs))
        return self.index_chunks(chunks)

    def index_chunks(self, chunks: Sequence[TextChunk]) -> int:
        with self._lock:
            added = self.index.add(list(chunks))
            if added:
                self.cache.clear()
                self.index.save()
            return added

    # ───────── 召回 ─────────
    def retrieve(self, query: str, top_k: Optional[int] = None,
                 permission: Optional[PermissionContext] = None,
                 channels: Optional[List[str]] = None) -> List[RetrievalHit]:
        with self._lock:
            return self._retrieve_locked(query, top_k, permission, channels)

    def _retrieve_locked(self, query, top_k=None, permission=None, channels=None):
        top_k = top_k or self.settings.retrieval_top_k
        cached = self.cache.get(query, permission, top_k) if channels is None else None
        if cached is not None:
            return cached

        active = [c for c in self.channels
                  if (channels is None or c.name in channels)
                  and (c.name != "graph" or (permission is not None and permission.is_admin))]
        with timer("rag.retrieve"):
            with ThreadPoolExecutor(max_workers=max(1, len(active))) as pool:
                futures = [pool.submit(c.retrieve, query, top_k, permission) for c in active]
                ranked = [f.result() for f in futures]
        fused = reciprocal_rank_fusion(ranked, k=self.settings.rrf_k, weights=_CHANNEL_WEIGHTS)
        results = rerank(query, fused, top_k, self.settings)
        if channels is None:
            self.cache.put(query, permission, top_k, results)
        return results

    def retrieve_evidence(self, query: str, top_k: Optional[int] = None,
                          permission: Optional[PermissionContext] = None) -> List[Evidence]:
        return [hit.to_evidence() for hit in self.retrieve(query, top_k, permission)]

    def cypher(self, query: str) -> GraphQueryResult:
        for channel in self.channels:
            if channel.name == "graph":
                return channel.cypher(query)  # type: ignore[attr-defined]
        from ..graph.cypher_lite import run_cypher

        return run_cypher(query, self.graph_store, include_evidence=True)

    def diagnose(self, query: str, permission: Optional[PermissionContext] = None) -> Dict[str, object]:
        """各通道命中情况，用于评测 Hit@5 与告警（空检索率）。"""
        report: Dict[str, object] = {}
        for channel in self.channels:
            if channel.name == "graph" and not (permission and permission.is_admin):
                continue
            hits = channel.retrieve(query, self.settings.retrieval_top_k, permission)
            report[channel.name] = {
                "hits": len(hits),
                "top": [h.chunk.doc_name for h in hits[:3]],
            }
        return report

    def save(self) -> None:
        self.index.save()

    def load(self) -> int:
        return self.index.load()


_engine: Optional[RetrievalEngine] = None


def get_retrieval_engine(settings: Optional[Settings] = None) -> RetrievalEngine:
    global _engine
    if _engine is None:
        _engine = RetrievalEngine(settings)
    return _engine
