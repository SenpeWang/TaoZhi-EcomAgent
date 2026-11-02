"""会话状态与生命周期管理器。

管理会话的多轮轨迹存储、动态上下文投影与版本回溯。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from .projection import build_session_projection


class SessionManager:
    """会话管理器，负责会话消息缓存与向模型送入前的投影编排。"""

    def __init__(self, conversation_id: str) -> None:
        """初始化会话管理器。

        Args:
            conversation_id: 会话唯一标识符。
        """
        self.conversation_id = conversation_id
        self._messages: List[Dict[str, Any]] = []

    def append_message(self, message: Dict[str, Any]) -> None:
        """向当前会话追加一条消息。

        Args:
            message: 消息字典。
        """
        self._messages.append(message)

    def get_messages(self) -> List[Dict[str, Any]]:
        """获取所有原始消息。

        Returns:
            List[Dict[str, Any]]: 完整消息列表。
        """
        return list(self._messages)

    def get_projected_context(self, max_retained_tools: int = 3) -> List[Dict[str, Any]]:
        """获取经过静态投影与旧工具瘦身后的上下文列表。

        Args:
            max_retained_tools: 最新保留工具数量。

        Returns:
            List[Dict[str, Any]]: 优化后的消息列表。
        """
        return build_session_projection(self._messages, max_retained_tool_steps=max_retained_tools)