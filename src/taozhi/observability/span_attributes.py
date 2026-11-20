"""内建链路详情允许存储和展示的 OpenTelemetry 属性。"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

from openinference.semconv.trace import SpanAttributes

from taozhi.domain.privacy import redact

SPAN_KINDS = {"CHAIN", "AGENT", "LLM", "TOOL", "RETRIEVER", "INTERNAL"}
_OTEL_KINDS = {"INTERNAL", "CLIENT", "SERVER", "PRODUCER", "CONSUMER"}
# SDK 使用 OpenInference 语义，数据库与查询 API 沿用既有字段，使跨版本用量可聚合。
_INFERENCE_FIELDS = {
    "gen_ai.request.model": SpanAttributes.LLM_MODEL_NAME,
    "gen_ai.agent.name": SpanAttributes.AGENT_NAME,
    "gen_ai.usage.input_tokens": SpanAttributes.LLM_TOKEN_COUNT_PROMPT,
    "gen_ai.usage.output_tokens": SpanAttributes.LLM_TOKEN_COUNT_COMPLETION,
    "tool": SpanAttributes.TOOL_NAME,
}
_STORAGE_FIELDS = {inference: stored for stored, inference in _INFERENCE_FIELDS.items()}
_IDENTIFIER = re.compile(r"[\w.,/-]{1,120}")
_TEXT = {
    "gen_ai.operation.name", "gen_ai.request.model", "gen_ai.agent.name",
    "tool", "tool.status", "subagent_id", "state.selected_agents", "error.type",
}
_NUMERIC = {
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "tool.result_count",
    "state.evidence", "state.findings", "state.missing", "state.business",
    "state.answer_chars", "state.model_calls",
}


def safe_attributes(attrs: Mapping | None) -> dict:
    """保留分类标识、用量及计数；提示词、工具参数、任意 JSON 和异常正文不入库。"""
    clean = {}
    for attribute, value in (attrs or {}).items():
        key = _STORAGE_FIELDS.get(attribute, attribute)
        if key in _TEXT and isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            clean[key] = redact(value)
        elif key in _NUMERIC and type(value) in (int, float) and 0 <= value <= 2**63 - 1 and math.isfinite(value):
            clean[key] = value
        elif key == "state.hard_block" and isinstance(value, bool):
            clean[key] = value
        elif key == SpanAttributes.OPENINFERENCE_SPAN_KIND and isinstance(value, str) and value in SPAN_KINDS:
            clean[key] = value
        elif key == "otel.span.kind" and isinstance(value, str) and value in _OTEL_KINDS:
            clean[key] = value
    return clean


def inference_attributes(attributes: Mapping | None) -> dict:
    """仅将已验证的运行指标传给 SDK 和采样器。"""
    return {_INFERENCE_FIELDS.get(key, key): value for key, value in safe_attributes(attributes).items()}
