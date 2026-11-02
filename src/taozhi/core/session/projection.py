"""会话动态投影与上下文净化器。

负责在向大语言模型递交上下文数组前，纯静态过滤掉陈旧轮次中庞大冗余的工具调用
返回体，将其替换为紧凑的占位摘要，实现零模型调用、零 Token 浪费的静态上下文净化。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_session_projection(
    messages: List[Dict[str, Any]],
    max_retained_tool_steps: int = 3,
) -> List[Dict[str, Any]]:
    """构建经过清洗投影的会话上下文消息列表。

    保持用户输入与助手最终研报完好无损，仅将早于最新 N 轮的历史工具超长输出静态瘦身。

    Args:
        messages: 完整的时序消息列表。
        max_retained_tool_steps: 最新保留全量工具输出的步数。

    Returns:
        List[Dict[str, Any]]: 投影瘦身后的高净值消息列表。
    """
    if len(messages) <= max_retained_tool_steps * 2:
        return [dict(m) for m in messages]

    projected: List[Dict[str, Any]] = []
    tool_counter = 0

    # 倒序遍历以识别最新若干个工具输出
    for msg in reversed(messages):
        msg_copy = dict(msg)
        is_tool = (
            msg.get("type") in ("TOOL_RESULT", "TOOL_CALL")
            or msg.get("role") in ("tool", "toolResult")
        )

        if is_tool:
            tool_counter += 1
            if tool_counter > max_retained_tool_steps:
                content = str(msg.get("content", ""))
                if len(content) > 120:
                    msg_copy["content"] = content[:80] + " ... [historical tool output pruned for token efficiency]"
        projected.append(msg_copy)

    projected.reverse()
    return projected