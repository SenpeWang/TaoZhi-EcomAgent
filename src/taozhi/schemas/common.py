"""通用请求基类与审批决断模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    """全局严格输入基类：禁止未声明字段并自动去除首尾空白。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Decision(Input):
    """人工审批通过或拒绝请求体。"""

    approve: bool
    comment: str = Field(default="", max_length=1000)
