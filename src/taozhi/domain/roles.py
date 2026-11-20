"""统一角色层级：系统管理员 > 老板 > 组长 > 员工（数值越大权限越高）。"""
from __future__ import annotations

from typing import Any, Iterable

ROLE_LEVEL_ADMIN = 4
ROLE_LEVEL_BOSS = 3
ROLE_LEVEL_LEADER = 2
ROLE_LEVEL_STAFF = 1

ROLE_HIERARCHY = {
    "系统管理员": ROLE_LEVEL_ADMIN,
    "admin": ROLE_LEVEL_ADMIN,
    "老板": ROLE_LEVEL_BOSS,
    "boss": ROLE_LEVEL_BOSS,
    "组长": ROLE_LEVEL_LEADER,
    "leader": ROLE_LEVEL_LEADER,
    "员工": ROLE_LEVEL_STAFF,
    "staff": ROLE_LEVEL_STAFF,
    "employee": ROLE_LEVEL_STAFF,
}


def get_role_level(role: str) -> int:
    """把角色名称换算为层级数值。

    Args:
        role: 角色名称，中英文均可，大小写不敏感。

    Returns:
        层级数值：系统管理员 4、老板 3、组长 2、员工 1，未知角色为 0。
    """
    return ROLE_HIERARCHY.get(str(role or "").strip().lower(), 0)


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
        (get_role_level(str(m.get("role") or "")) for m in memberships),
        default=ROLE_LEVEL_STAFF,
    )
