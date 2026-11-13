"""专职电商子智能体注册中心（向后兼容桥接层）。"""
from __future__ import annotations

from ..core.agent.registry import SubagentRegistry, default_registry

__all__ = ["SubagentRegistry", "default_registry"]
