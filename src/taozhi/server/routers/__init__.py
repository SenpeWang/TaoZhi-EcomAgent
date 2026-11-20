"""FastAPI 分领域 HTTP 路由集合。"""
from __future__ import annotations

from importlib import import_module

__all__ = ["admin", "auth", "documents", "system", "tasks", "traces"]


def __getattr__(name: str):
    """按需加载子模块，独立用例只引入自身需要的依赖。"""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(name)
