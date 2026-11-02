"""Token 计量与用量统计模块。

提供大模型交互的 Token 消耗监控、解析与指标记录。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict


def extract_usage(response_message: Any) -> Dict[str, int]:
    """从模型响应消息中提取 Token 用量数据。

    Args:
        response_message: LangChain 或 OpenAI 返回的响应消息对象。

    Returns:
        Dict[str, int]: 包含 prompt_tokens 与 completion_tokens 的字典。
    """
    meta = getattr(response_message, "response_metadata", {}) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
