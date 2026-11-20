"""Token 计量与用量统计模块。

提供大模型交互的 Token 消耗监控、解析与指标记录。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict


def _token_count(value: Any) -> int:
    """缺失或异常的供应商计量字段按零计，避免计量触发模型重试。"""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def extract_usage(response_message: Any) -> Dict[str, int]:
    """从模型响应消息中提取 Token 用量数据。

    Args:
        response_message: LangChain 或 OpenAI 返回的响应消息对象。

    Returns:
        Dict[str, int]: 包含 prompt_tokens 与 completion_tokens 的字典。
    """
    meta = getattr(response_message, "response_metadata", {}) or {}
    usage = (meta.get("token_usage") or meta.get("usage")) if isinstance(meta, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    normalized = getattr(response_message, "usage_metadata", {}) or {}
    normalized = normalized if isinstance(normalized, dict) else {}
    prompt_tokens = _token_count(usage.get("prompt_tokens", normalized.get("input_tokens")))
    completion_tokens = _token_count(usage.get("completion_tokens", normalized.get("output_tokens")))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": _token_count(usage.get("total_tokens") or normalized.get("total_tokens")) or (prompt_tokens + completion_tokens),
    }
