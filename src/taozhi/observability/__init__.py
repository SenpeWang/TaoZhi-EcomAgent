"""可观测性横切能力层：全链路结构化 Trace、指标埋点与审计。"""
from .tracer import TraceCollector, active_trace, get_current_trace

__all__ = ["TraceCollector", "active_trace", "get_current_trace"]