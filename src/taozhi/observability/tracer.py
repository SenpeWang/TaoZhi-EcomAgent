"""全链路结构化 Trace 树收集器与上下文管理。"""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from ..schemas.trace import Span, TraceContext, TraceRecord

_current_trace: contextvars.ContextVar[Optional[TraceCollector]] = contextvars.ContextVar(
    "_current_trace", default=None
)


class TraceCollector:
    """结构化 Trace 树收集器。支持跨层级 Span 嵌套、耗时度量与模型用量统计。"""

    def __init__(
        self,
        tenant_id: str = "default",
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.context = TraceContext(
            trace_id=trace_id or TraceContext().trace_id,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        self.root_span = Span(name="root", kind="root")
        self.context.root_span = self.root_span
        self._span_stack: List[Span] = [self.root_span]
        self.model_calls: int = 0
        self.total_tokens: int = 0

    @property
    def current_span(self) -> Span:
        return self._span_stack[-1]

    @contextmanager
    def span(
        self,
        name: str,
        kind: str = "internal",
        input_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Generator[Span, None, None]:
        """开启子阶段 Span 上下文。自动挂载至父 Span 下并在退出时度量耗时。"""
        parent = self.current_span
        new_span = Span(
            name=name,
            kind=kind,
            parent_id=parent.span_id,
            input_data=input_data,
            metadata=metadata or {},
        )
        parent.children.append(new_span)
        self._span_stack.append(new_span)
        error_msg: Optional[str] = None
        try:
            yield new_span
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            new_span.finish(error=error_msg)
            self._span_stack.pop()

    def record_llm_call(self, tokens: int = 0) -> None:
        """记录模型调用次数与 Token 用量。"""
        self.model_calls += 1
        self.total_tokens += max(0, tokens)

    def finish(self, error: Optional[str] = None) -> TraceRecord:
        """结束全局 Trace 并生成固化的 TraceRecord。"""
        self.root_span.finish(error=error)
        return TraceRecord(
            trace_id=self.context.trace_id,
            session_id=self.context.session_id,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            root_span=self.root_span,
            total_duration_ms=self.root_span.duration_ms,
            model_calls=self.model_calls,
            total_tokens=self.total_tokens,
        )


def get_current_trace() -> Optional[TraceCollector]:
    """获取当前协程/线程上下文中的 TraceCollector。"""
    return _current_trace.get()


@contextmanager
def active_trace(
    tenant_id: str = "default",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Generator[TraceCollector, None, None]:
    """激活全局 Trace 上下文环境。"""
    collector = TraceCollector(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    token = _current_trace.set(collector)
    try:
        yield collector
    finally:
        _current_trace.reset(token)