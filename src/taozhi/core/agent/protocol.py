"""多智能体沙箱隔离模式协议定义。"""

from __future__ import annotations

from enum import Enum


class WorkspaceMode(str, Enum):
    """工作区隔离模式（支持 inherit / branch / share 模式）。"""

    INHERIT = "inherit"  # 共享父级工作区上下文
    BRANCH = "branch"   # 完全独立的分支沙箱（隔离 scratch 与临时文件）
    SHARE = "share"     # 共享存储只读引用，独立工作区写入
