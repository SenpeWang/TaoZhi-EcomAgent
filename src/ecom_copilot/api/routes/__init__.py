"""API 路由包。"""

from . import graph, health, knowledge, mcp, research  # noqa: F401

__all__ = ["research", "graph", "knowledge", "mcp", "health"]
