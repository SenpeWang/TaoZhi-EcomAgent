"""可观测性横切能力层：全链路结构化 Trace、指标埋点与审计。"""
from .tracing import current_trace_id, node_span, setup_tracing

__all__ = ["current_trace_id", "node_span", "setup_tracing"]