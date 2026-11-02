"""上下文切分点安全计算模块。

计算会话消息流的安全截断点，严格保障工具调用（tool_calls）与工具结果（tool_result）
的一对一对齐，绝不允许将工具执行结果孤立截断，规避模型协议解析错误。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict, List


def is_cut_point_message(message: Dict[str, Any]) -> bool:
    """判断消息是否可作为安全切分点。

    User 消息、Assistant 完整答复或阶段小结可作为安全切断点；
    孤立的 tool_result 严禁作为切分起点。

    Args:
        message: 会话轨迹中的单条消息字典。

    Returns:
        bool: True 表示可以从此处切断，False 表示存在依赖不能切断。
    """
    msg_type = message.get("type", "").upper()
    role = message.get("role", "").lower()

    if msg_type == "TOOL_RESULT" or role == "tool":
        return False
    return True


def find_safe_cut_index(
    messages: List[Dict[str, Any]],
    keep_recent_count: int = 6,
) -> int:
    """寻找保留近期无损轮次的最优安全切分点下标。

    Args:
        messages: 完整的按时序排列的消息列表。
        keep_recent_count: 预期保留的最少近期消息数量。

    Returns:
        int: 第一个需要被保留的消息索引。
    """
    if len(messages) <= keep_recent_count:
        return 0

    target_idx = len(messages) - keep_recent_count
    for i in range(target_idx, -1, -1):
        if is_cut_point_message(messages[i]):
            return i

    return target_idx