"""可观测性包：指标与审计事件。

Trace 曾由 tracing 模块承担，随离线编排链路一并下线；审计改由
enterprise.policy.audit 落 audit_events 表。
"""

from .metrics import (  # noqa: F401
    agent_success_rate,
    inc,
    log_event,
    observe,
    percentile,
    rag_hit_rate,
    record_agent,
    record_llm_call,
    record_llm_error,
    record_retrieval,
    recent_events,
    reset,
    snapshot,
    timer,
)

__all__ = [
    "agent_success_rate", "inc", "log_event", "observe", "percentile",
    "rag_hit_rate", "record_agent", "record_llm_call", "record_llm_error",
    "record_retrieval", "recent_events", "reset", "snapshot", "timer",
]
