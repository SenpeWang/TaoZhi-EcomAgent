"""企业文档管理、版本修订、密级策略与单文档授权请求模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from taozhi.schemas.common import Input


class Doc(Input):
    """新增或上传文档元数据请求体。"""

    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=200000)
    node_id: str
    scope: Literal["company", "org", "selected"] = "org"
    level: int = Field(default=2, ge=1, le=3)


class EditDoc(Input):
    """修订已发布或草稿文档正文请求体。"""

    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=200000)
    version: int = Field(ge=1)


class DocPolicy(Input):
    """文档可见范围、密级与 AI/下载权限策略变更请求体。"""

    scope: Literal["company", "org", "selected"] | None = None
    node_id: str | None = None
    level: int | None = Field(default=None, ge=1, le=3)
    ai_allowed: bool | None = None
    download_allowed: bool | None = None


class Grant(Input):
    """单文档个人特例授权或吊销审批请求体。"""

    user_id: str
    effect: Literal["allow", "deny"] = "allow"
    can_download: bool = False
    expires_at: datetime | None = None
    revoke: bool = False
