"""电商情报类 MCP 标准服务端实现（EcommerceMcpServer）。

基于 Anthropic MCP 协议规范，将 1688、亚马逊、全网搜索与网页去噪统一暴露为标准 MCP 工具集。
严格遵循 Google Python Style 规范。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from ...core.tools.search_1688 import search_1688_wholesale
from ...core.tools.search_amazon import search_amazon
from ...core.tools.search_web import search_web
from ...core.tools.web_scraper import fetch_web_page
from .protocol import McpCallToolResult, McpContentItem, McpToolDefinition


def _call_global(name: str, **kwargs: Any) -> Any:
    """按名称解析模块级工具函数，支持测试注入或运行时替换。"""
    fn = globals()[name]
    return fn(**kwargs)


class EcommerceMcpServer:
    """电商与市场情报类 MCP 服务端（支持 tools/list 与 tools/call）。"""

    def __init__(self) -> None:
        self._tools: Dict[str, McpToolDefinition] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._aliases: Dict[str, str] = {"search_1688": "search_1688_wholesale"}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册内置的 4 大电商情报类 MCP 工具。"""
        # 1. Amazon.com 跨境电商出海情报搜索
        self.register_tool(
            McpToolDefinition(
                name="search_amazon",
                description="在亚马逊全球站 (https://www.amazon.com/) 检索跨境商品、零售价($)、ASIN、评分与买家评论。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "英文或中文检索关键词（例如 iphone 16 case）"},
                        "limit": {"type": "integer", "description": "返回商品条数上限", "default": 10},
                        "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 12.0},
                    },
                    "required": ["query"],
                },
            ),
            handler=lambda args: _call_global(
                "search_amazon",
                query=str(args.get("query", "")),
                limit=int(args.get("limit", 10)),
                **(
                    {"timeout": float(args["timeout"])}
                    if args.get("timeout") is not None
                    else {}
                ),
            ),
        )

        # 2. 1688 源头工厂出厂货源比价
        self.register_tool(
            McpToolDefinition(
                name="search_1688_wholesale",
                description="深入 1688 批发渠道抓取源头工厂批发底价、出厂阶梯价格、起订量(MOQ)与供应商分布。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "1688 检索关键词"},
                        "limit": {"type": "integer", "description": "返回条目上限", "default": 20},
                        "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 90.0},
                    },
                    "required": ["keyword"],
                },
            ),
            handler=lambda args: _call_global(
                "search_1688_wholesale",
                keyword=str(args.get("keyword") or args.get("query") or ""),
                limit=int(args.get("limit", 20)),
                **(
                    {"timeout": float(args["timeout"])}
                    if args.get("timeout") is not None
                    else {}
                ),
            ),
        )

        # 3. 全网电商趋势与舆情搜索
        self.register_tool(
            McpToolDefinition(
                name="search_web",
                description="全网实时搜索电商商品的最新行业趋势、社媒痛点、主流品牌卖点与用户舆论。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词或调研主题"},
                        "limit": {"type": "integer", "description": "返回条数上限", "default": 10},
                        "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 12.0},
                    },
                    "required": ["query"],
                },
            ),
            handler=lambda args: _call_global(
                "search_web",
                query=str(args.get("query", "")),
                limit=int(args.get("limit", 10)),
                **(
                    {"timeout": float(args["timeout"])}
                    if args.get("timeout") is not None
                    else {}
                ),
            ),
        )

        # 4. 网页正文深度抓取与去噪清洗
        self.register_tool(
            McpToolDefinition(
                name="fetch_web_page",
                description="抓取指定网页 HTML 并清洗提取核心纯净正文文本，去除 script、style、导航栏及标签噪音。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "目标网页 URL"},
                        "max_chars": {"type": "integer", "description": "最多截取的纯文字数", "default": 4000},
                        "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 10.0},
                    },
                    "required": ["url"],
                },
            ),
            handler=lambda args: _call_global(
                "fetch_web_page",
                url=str(args.get("url", "")),
                max_chars=int(args.get("max_chars", 4000)),
                **(
                    {"timeout": float(args["timeout"])}
                    if args.get("timeout") is not None
                    else {}
                ),
            ),
        )

    def register_tool(
        self, definition: McpToolDefinition, handler: Callable[[Dict[str, Any]], Any]
    ) -> None:
        """注册一个新的 MCP 工具定义与处理函数。"""
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler

    def list_tools(self) -> List[McpToolDefinition]:
        """符合 MCP tools/list 协议接口规范。"""
        return list(self._tools.values())

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> McpCallToolResult:
        """符合 MCP tools/call 协议接口规范。"""
        canonical = self._aliases.get(name, name)
        if canonical not in self._handlers:
            return McpCallToolResult(
                content=[McpContentItem(type="text", text=f"Error: Tool '{name}' not found on MCP server.")],
                isError=True,
            )

        try:
            handler = self._handlers[canonical]
            raw_output = handler(arguments or {})
            if isinstance(raw_output, (dict, list)):
                text_payload = json.dumps(raw_output, ensure_ascii=False, indent=2)
            else:
                text_payload = str(raw_output)

            return McpCallToolResult(
                content=[McpContentItem(type="text", text=text_payload)],
                isError=False,
            )
        except Exception as exc:
            return McpCallToolResult(
                content=[McpContentItem(type="text", text=f"Execution error: {type(exc).__name__}: {str(exc)}")],
                isError=True,
            )


default_mcp_server = EcommerceMcpServer()
