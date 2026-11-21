"""内建链路详情允许存储和展示的 OpenTelemetry 属性。"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

from openinference.semconv.trace import RerankerAttributes, SpanAttributes

from taozhi.domain.privacy import redact

SPAN_KINDS = {"CHAIN", "AGENT", "LLM", "TOOL", "RETRIEVER", "EMBEDDING", "RERANKER", "INTERNAL"}
_OTEL_KINDS = {"INTERNAL", "CLIENT", "SERVER", "PRODUCER", "CONSUMER"}
_IDENTIFIER = re.compile(r"[\w.,/-]{1,120}")
_TEXT = {
    SpanAttributes.LLM_MODEL_NAME, SpanAttributes.AGENT_NAME, SpanAttributes.TOOL_NAME,
    SpanAttributes.EMBEDDING_MODEL_NAME, RerankerAttributes.RERANKER_MODEL_NAME,
    "llm.endpoint_id", "tool.status", "subagent_id", "state.selected_agents", "error.type",
}
_NUMERIC = {
    SpanAttributes.LLM_TOKEN_COUNT_PROMPT, SpanAttributes.LLM_TOKEN_COUNT_COMPLETION,
    SpanAttributes.LLM_TOKEN_COUNT_TOTAL, "retrieval.result_count", "tool.result_count",
    "state.evidence", "state.findings", "state.missing", "state.business",
    "context.evidence_count", "context.observation_count", "context.tool_count",
    "context.input_chars", "context.output_chars",
    "state.answer_chars", "state.model_calls", "research.dispatched_count",
}


def safe_attributes(attrs: Mapping | None) -> dict:
    """保留分类标识、用量及计数；提示词、工具参数、任意 JSON 和异常正文不入库。"""
    clean = {}
    for key, value in (attrs or {}).items():
        if key in _TEXT and isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            clean[key] = redact(value)
        elif key in _NUMERIC and type(value) in (int, float) and 0 <= value <= 2**63 - 1 and math.isfinite(value):
            clean[key] = value
        elif key in {"state.hard_block", "tool.dispatched"} and isinstance(value, bool):
            clean[key] = value
        elif key == "research.next_action" and value in ("dispatch_subagents", "synthesizer", "final_node"):
            clean[key] = value
        elif key == SpanAttributes.OPENINFERENCE_SPAN_KIND and isinstance(value, str) and value in SPAN_KINDS:
            clean[key] = value
        elif key == "otel.span.kind" and isinstance(value, str) and value in _OTEL_KINDS:
            clean[key] = value
    return clean
