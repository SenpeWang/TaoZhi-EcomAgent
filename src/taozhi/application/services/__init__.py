"""应用层业务用例服务：账号组织审批、文档全生命周期与智能问答调研执行。"""
from __future__ import annotations

from importlib import import_module

__all__ = ["admin_service", "document_service", "task_service", "trace_service"]


def __getattr__(name: str):
    """按需加载子模块，独立用例只引入自身需要的依赖。"""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(name)
