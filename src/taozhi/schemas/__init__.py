"""接口层请求数据传输对象（DTO）模型。"""
from __future__ import annotations

from taozhi.schemas.admin import Membership, NewUser, Org, OrgChange, UserChange
from taozhi.schemas.auth import Login, Password
from taozhi.schemas.common import Decision, Input
from taozhi.schemas.document import Doc, DocPolicy, EditDoc, Grant
from taozhi.schemas.task import Question, SteerInput, Tool

__all__ = [
    "Decision",
    "Doc",
    "DocPolicy",
    "EditDoc",
    "Grant",
    "Input",
    "Login",
    "Membership",
    "NewUser",
    "Org",
    "OrgChange",
    "Password",
    "Question",
    "SteerInput",
    "Tool",
    "UserChange",
]
