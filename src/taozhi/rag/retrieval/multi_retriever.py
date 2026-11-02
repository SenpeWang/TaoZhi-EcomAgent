"""多路并行混合检索器：实现 BM25 + 向量检索前置权限过滤与 RRF 融合。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...observability.tracer import get_current_trace
from ...schemas.rag import DocumentChunk, RetrievalCandidate, RetrievalFilter, RetrievalRequest

logger = logging.getLogger(__name__)


class MultiRetriever:
    """多路混合检索器门面。保证检索前置权限过滤，输出结构化候选切片。"""

    def __init__(self, db_conn_factory=None) -> None:
        self._db_conn_factory = db_conn_factory

    def _get_connection(self):
        if self._db_conn_factory:
            return self._db_conn_factory()
        try:
            from ...knowledge.db import database_connection
            return database_connection()
        except ImportError:
            return None

    def retrieve(self, request: RetrievalRequest) -> List[RetrievalCandidate]:
        """执行多路检索。内建 Pre-Retrieval 权限拦截与 Trace 树打点。"""
        trace = get_current_trace()
        span_ctx = trace.span(
            "rag.retrieval",
            kind="retrieval",
            input_data={
                "query": request.query,
                "tenant_id": request.filter.tenant_id,
                "max_security_level": request.filter.max_security_level,
                "top_k": request.top_k,
            },
        ) if trace else None

        candidates: List[RetrievalCandidate] = []
        try:
            if span_ctx:
                with span_ctx as s:
                    candidates = self._do_retrieve(request)
                    s.output_data = {
                        "candidate_count": len(candidates),
                        "top_scores": [c.score for c in candidates[:3]],
                    }
            else:
                candidates = self._do_retrieve(request)
            return candidates
        except Exception as exc:
            logger.warning("RAG 多路检索执行降级: %s", exc)
            return []

    def _do_retrieve(self, request: RetrievalRequest) -> List[RetrievalCandidate]:
        if not request.query.strip():
            return []

        # 构造安全执行主体 (Principal)，确保权限过滤下推到 SQL/ES 过滤条件中
        from ...server.policy import Principal
        principal = Principal(
            user_id="system_retriever",
            tenant_id=request.filter.tenant_id,
            clearance=request.filter.max_security_level,
            roles=["staff"],
            departments=request.filter.department_ids or [],
            authorized_docs=set(request.filter.authorized_doc_ids or []),
            active=True,
            version=1,
        )

        conn_cm = self._get_connection()
        if not conn_cm:
            return []

        with conn_cm as conn:
            from ...knowledge.retriever import search_documents
            raw_chunks = search_documents(conn, principal, request.query, limit=request.top_k)
            if not raw_chunks:
                return []

            candidates: List[RetrievalCandidate] = []
            for rank, c in enumerate(raw_chunks, 1):
                chunk_id = str(c.get("id") or f"chk_{rank}")
                doc_id = str(c.get("document_id") or "doc_unknown")
                doc_title = str(c.get("document_title") or c.get("title") or "企业业务资料")
                text = str(c.get("text") or c.get("content") or "")
                score = float(c.get("score") or (1.0 / (60 + rank)))

                chunk_obj = DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_title=doc_title,
                    content=text,
                    security_level=int(c.get("security_level") or 0),
                    page_number=c.get("page_number"),
                    line_start=c.get("line_start"),
                    line_end=c.get("line_end"),
                    metadata=c.get("metadata") or {},
                )
                candidates.append(
                    RetrievalCandidate(
                        chunk=chunk_obj,
                        score=round(score, 4),
                        channel="hybrid",
                    )
                )
            return candidates