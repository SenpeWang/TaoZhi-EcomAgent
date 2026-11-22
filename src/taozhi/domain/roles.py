"""统一角色层级：系统管理员 > 老板 > 组长 > 员工（数值越大权限越高）。"""
from __future__ import annotations

from typing import Any, Iterable

ROLE_LEVEL_ADMIN = 4
ROLE_LEVEL_BOSS = 3
ROLE_LEVEL_LEADER = 2
ROLE_LEVEL_STAFF = 1

_MEMBERSHIP_LEVELS = {
    "boss": ROLE_LEVEL_BOSS,
    "leader": ROLE_LEVEL_LEADER,
    "employee": ROLE_LEVEL_STAFF,
}


def derive_role_level(system_admin: bool, memberships: Iterable[dict[str, Any]]) -> int:
    """由系统管理员标记与任职角色推导主体层级。

    Args:
        system_admin: 数据库中的系统管理员标记。
        memberships: 该用户的有效任职列表，每项含 ``role`` 字段。

    Returns:
        主体层级数值，取管理员标记与各任职角色层级中的最大值；无任职时按员工处理。
    """
    if system_admin:
        return ROLE_LEVEL_ADMIN
    return max(
        (_MEMBERSHIP_LEVELS.get(str(m.get("role", "")), 0) for m in memberships),
        default=ROLE_LEVEL_STAFF,
    )


def is_system_admin(level: int) -> bool:
    """判断角色层级是否达到系统管理员。

    Args:
        level: 主体层级数值。

    Returns:
        达到管理员层级返回 True。
    """
    return level >= ROLE_LEVEL_ADMIN
