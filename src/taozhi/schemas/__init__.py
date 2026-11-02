"""TaoZhi 契约层：请求/响应 DTO、Agent 状态、Trace 结构与 RAG 规范。"""
from .chat import ChatRequest, ChatResponse, CitationItem, StreamChunk
from .rag import DocumentChunk, RetrievalCandidate, RetrievalFilter, RetrievalRequest
from .trace import Span, TraceContext, TraceRecord

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "CitationItem",
    "StreamChunk",
    "DocumentChunk",
    "RetrievalCandidate",
    "RetrievalFilter",
    "RetrievalRequest",
    "Span",
    "TraceContext",
    "TraceRecord",
]