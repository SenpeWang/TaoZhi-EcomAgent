"""Anthropic Model Context Protocol (MCP) 核心协议数据模型。

遵循 MCP 标准规范（Tools 规范），定义跨模型与跨进程工具调用契约。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class McpToolDefinition(BaseModel):
    """MCP 工具标准元数据规范。"""
    name: str = Field(..., description="工具唯一标识名称")
    description: str = Field(..., description="工具功能与用途语义说明")
    inputSchema: Dict[str, Any] = Field(..., description="符合 JSON Schema 规范的入参定义")


class McpContentItem(BaseModel):
    """MCP 响应内容项。"""
    type: str = Field("text", description="内容类型，默认为 text")
    text: str = Field(..., description="工具返回的文本内容或序列化载荷")


class McpCallToolResult(BaseModel):
    """MCP 工具调用统一返回结果。"""
    content: List[McpContentItem] = Field(default_factory=list, description="结果内容数组")
    isError: bool = Field(False, description="工具执行是否遭遇异常")