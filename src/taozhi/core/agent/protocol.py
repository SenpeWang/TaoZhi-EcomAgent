"""多智能体沙箱隔离模式协议定义。"""

from __future__ import annotations

from enum import Enum


class WorkspaceMode(str, Enum):
    """工作区隔离模式（支持 inherit / branch / share 模式）。"""

    INHERIT = "inherit"
    BRANCH = "branch"
    SHARE = "share"
