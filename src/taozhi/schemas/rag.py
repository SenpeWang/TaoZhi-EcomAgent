"""RAG 检索能力层契约：切片、检索请求、前置元数据过滤器与引用候选。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RetrievalFilter(BaseModel):
    """前置权限与元数据过滤器：确保检索前拦截，绝不发生事后裁剪。"""
    tenant_id: str
    max_security_level: int = 0
    authorized_doc_ids: Optional[List[str]] = None
    department_ids: Optional[List[str]] = None
    metadata_filters: Dict[str, Any] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """切片不可变实体：携带行号范围与所属文档版本。"""
    chunk_id: str
    doc_id: str
    doc_title: str
    content: str
    security_level: int = 0
    page_number: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalCandidate(BaseModel):
    """带评分的召回候选切片。"""
    chunk: DocumentChunk
    score: float
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    rerank_score: Optional[float] = None
    channel: str = "hybrid"  # bm25, vector, hybrid


class RetrievalRequest(BaseModel):
    """统一在线检索请求。"""
    query: str
    filter: RetrievalFilter
    top_k: int = 5
    rerank: bool = True
    enable_hybrid: bool = True


class CitationItem(BaseModel):
    """回答中内嵌的事实依据引用锚点。"""
    citation_id: str
    doc_id: str
    doc_title: str
    chunk_id: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    snippet: str