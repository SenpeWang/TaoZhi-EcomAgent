"""用户管理、组织架构与成员任职请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from taozhi.schemas.common import Input


class NewUser(Input):
    """新建企业账号请求体。"""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=80)


class UserChange(Input):
    """用户属性或停用变更审批请求体。"""

    display_name: str | None = Field(default=None, max_length=80)
    active: bool | None = None
    system_admin: bool | None = None
    new_password: str | None = Field(default=None, max_length=128)


class Membership(Input):
    """组织任职授予或解除审批请求体。"""

    node_id: str
    role: Literal["employee", "leader", "boss"]
    remove: bool = False


class Org(Input):
    """新增部门或小组请求体。"""

    name: str = Field(min_length=1, max_length=80)
    kind: Literal["department", "team"]
    parent_id: str


class OrgChange(Input):
    """组织节点名称、父节点或停用调整审批请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: str | None = None
    active: bool | None = None
