"""认证与口令修改请求模型。"""
from __future__ import annotations

from pydantic import Field

from taozhi.schemas.common import Input


class Login(Input):
    """账号登录请求体。"""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    tenant_code: str = ""


class Password(Input):
    """修改个人登录密码请求体。"""

    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)
