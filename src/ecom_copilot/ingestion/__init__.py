"""数据采集层：只保留注入防护。

抓取适配器（base / sources）随离线调研链路一并下线——它们依赖已删除的
schemas 与本地索引，生产链路只从 guard 取注入检测。
"""

from .guard import (  # noqa: F401
    assert_tool_allowed,
    allowed_tools,
    detect_injection,
    is_safe,
    sanitize,
)
__all__ = ["assert_tool_allowed", "allowed_tools", "detect_injection", "is_safe", "sanitize"]
