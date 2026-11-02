"""接入层协议契约：同步问答、流式传输、会话 DTO 与响应封装。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .rag import CitationItem


class ChatRequest(BaseModel):
    """接入层问答请求。只做参数校验与协议适配，不含编排逻辑。"""
    question: str = Field(..., min_length=1, max_length=4000, description="用户提问内容")
    session_id: Optional[str] = Field(None, description="所属会话 ID")
    stream: bool = Field(False, description="是否开启 SSE 流式输出")
    temperature: float = Field(0.2, ge=0.0, le=1.0, description="模型采样温度")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="透传元数据")


class ChatResponse(BaseModel):
    """接入层问答统一响应体。"""
    answer: str = Field(..., description="带引用锚点的回答文本")
    citations: List[CitationItem] = Field(default_factory=list, description="事实引用依据")
    session_id: str = Field(..., description="关联会话 ID")
    trace_id: str = Field(..., description="全链路可观测性 Trace ID")
    duration_ms: float = Field(0.0, description="总耗时(毫秒)")
    is_fallback: bool = Field(False, description="是否触发了搜索或资料不足降级")
    cached: bool = Field(False, description="是否命中语义缓存直达")


class StreamChunk(BaseModel):
    """流式事件块。"""
    event: str = Field(..., description="事件类别: delta, citation, state, error, done")
    data: Dict[str, Any] = Field(default_factory=dict, description="事件载荷")