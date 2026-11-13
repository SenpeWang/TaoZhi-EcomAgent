"""全链路结构化 Trace 树与可观测性契约（由基础设施层模型导出）。"""
from __future__ import annotations

from ..observability.models import Span, TraceContext, TraceRecord

__all__ = ["Span", "TraceContext", "TraceRecord"]
