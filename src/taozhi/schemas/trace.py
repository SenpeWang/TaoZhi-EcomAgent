"""全链路结构化 Trace 树与可观测性契约。"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Span(BaseModel):
    """Trace 树中的单一执行阶段/跨度。"""
    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    name: str
    kind: str = "internal"  # internal, tool, retrieval, llm, cache, agent
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: float = 0.0
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    children: List[Span] = Field(default_factory=list)

    def finish(self, error: Optional[str] = None) -> None:
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        if error:
            self.error = error


class TraceContext(BaseModel):
    """当前任务执行的全局 Trace 上下文。"""
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: Optional[str] = None
    tenant_id: str = "default"
    user_id: Optional[str] = None
    root_span: Optional[Span] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TraceRecord(BaseModel):
    """可持久化或投递至观测系统的 Trace 记录。"""
    trace_id: str
    session_id: Optional[str] = None
    tenant_id: str
    user_id: Optional[str] = None
    root_span: Span
    total_duration_ms: float
    model_calls: int = 0
    total_tokens: int = 0
    created_at: float = Field(default_factory=time.time)