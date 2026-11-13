"""面向非人类消费者的受控工具抽象基类：具备有界输出、错误引导与演练能力（向后兼容导出）。"""
from __future__ import annotations

from ...core.tools.base import BaseTool, ToolContext, ToolResult

__all__ = ["BaseTool", "ToolContext", "ToolResult"]
