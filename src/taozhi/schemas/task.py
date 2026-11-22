"""问答、多智能体协同调研、干预指令与工具调用请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from taozhi.schemas.common import Input


class SteerInput(Input):
    """运行中协同调研任务中途动态干预请求体。"""

    input: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class Question(Input):
    """智能问答与市场协同调研任务提交请求体。"""

    question: str = Field(min_length=1, max_length=4000)
    kind: Literal["ask", "research"] = "ask"
    platforms: list[str] = Field(default_factory=list, max_length=8)
    node_id: str = ""
    input_level: int = Field(default=1, ge=1, le=3)
    conversation_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    require_review: bool = False
    allow_external: bool = True
    allow_public_reference: bool = True
    sku_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    order_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    keyword: str = Field(default="", max_length=100)


class Tool(Input):
    """直接受控工具调用请求体。"""

    name: Literal["search_knowledge", "query_product_spec", "query_compatibility", "query_stock", "query_order"]
    query: str = Field(default="", max_length=4000)
    identifier: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
