"""电商情报类 MCP 标准服务端实现（EcommerceMcpServer）。

基于 Anthropic MCP 协议规范，将 1688、亚马逊、全网搜索与网页去噪统一暴露为标准 MCP 工具集。
严格遵循 Google Python Style 规范。
"""
from __future__ import annotations

import json
import subprocess

import httpx
from typing import Any, Callable, Dict, List, Optional

from taozhi.core.tools.outcome import ToolInvocationError
from taozhi.domain.trace_diagnostic import CATALOG
from taozhi.observability.tracing import record_diagnostic
from ...core import tools
from .protocol import McpCallToolResult, McpContentItem, McpToolDefinition


def _build_handler(tool_name: str, arg_spec: tuple[tuple[str, tuple[str, ...], Any, Any], ...]) -> Callable[[Dict[str, Any]], Any]:
    """根据声明式参数规格构造标准 MCP 工具调用适配器。"""

    def _handle(args: Dict[str, Any]) -> Any:
        kwargs: Dict[str, Any] = {}
        for target_key, source_keys, default_val, caster in arg_spec:
            raw_val = next((args.get(k) for k in source_keys if args.get(k) is not None), None)
            if raw_val is None:
                if default_val is None:
                    continue
                raw_val = default_val
            kwargs[target_key] = caster(raw_val)
        return getattr(tools, tool_name)(**kwargs)

    return _handle


class EcommerceMcpServer:
    """电商与市场情报类 MCP 服务端（支持 tools/list 与 tools/call）。"""

    _BUILTIN_SPECS: tuple[tuple[str, str, Dict[str, Any], tuple[tuple[str, tuple[str, ...], Any, Any], ...]], ...] = (
        (
            "search_amazon",
            "在亚马逊全球站 (https://www.amazon.com/) 检索跨境商品、零售价($)、ASIN、评分与买家评论。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "英文或中文检索关键词（例如 iphone 16 case）"},
                    "limit": {"type": "integer", "description": "返回商品条数上限", "default": 10},
                    "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 12.0},
                },
                "required": ["query"],
            },
            (("query", ("query",), "", str), ("limit", ("limit",), 10, int), ("timeout", ("timeout",), None, float)),
        ),
        (
            "search_1688_wholesale",
            "深入 1688 批发渠道抓取源头工厂批发底价、出厂阶梯价格、起订量(MOQ)与供应商分布。",
            {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "1688 检索关键词"},
                    "limit": {"type": "integer", "description": "返回条目上限", "default": 20},
                    "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 90.0},
                },
                "required": ["keyword"],
            },
            (("keyword", ("keyword", "query"), "", str), ("limit", ("limit",), 20, int), ("timeout", ("timeout",), None, float)),
        ),
        (
            "search_web",
            "全网实时搜索电商商品的最新行业趋势、社媒痛点、主流品牌卖点与用户舆论。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或调研主题"},
                    "limit": {"type": "integer", "description": "返回条数上限", "default": 10},
                    "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 12.0},
                },
                "required": ["query"],
            },
            (("query", ("query",), "", str), ("limit", ("limit",), 10, int), ("timeout", ("timeout",), None, float)),
        ),
        (
            "fetch_web_page",
            "抓取指定网页 HTML 并清洗提取核心纯净正文文本，去除 script、style、导航栏及标签噪音。",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 URL"},
                    "max_chars": {"type": "integer", "description": "最多截取的纯文字数", "default": 4000},
                    "timeout": {"type": "number", "description": "可选单次超时时间（秒）", "default": 10.0},
                },
                "required": ["url"],
            },
            (("url", ("url",), "", str), ("max_chars", ("max_chars",), 4000, int), ("timeout", ("timeout",), None, float)),
        ),
    )

    def __init__(self) -> None:
        self._tools: Dict[str, McpToolDefinition] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册内置的 4 大电商情报类 MCP 工具。"""
        for name, desc, schema, arg_spec in self._BUILTIN_SPECS:
            self.register_tool(
                McpToolDefinition(name=name, description=desc, inputSchema=schema),
                handler=_build_handler(name, arg_spec),
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
        if name not in self._handlers:
            return McpCallToolResult(
                content=[McpContentItem(type="text", text=f"Error: Tool '{name}' not found on MCP server.")],
                isError=True,
            )

        try:
            handler = self._handlers[name]
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
            timed_out = isinstance(exc, (TimeoutError, httpx.TimeoutException, subprocess.TimeoutExpired))
            code = exc.code if isinstance(exc, ToolInvocationError) else ("tool.timeout" if timed_out else "tool.error")
            record_diagnostic(code, "failed", exc)
            return McpCallToolResult(
                content=[McpContentItem(type="text", text=CATALOG[code][1])],
                isError=True,
            )


default_mcp_server = EcommerceMcpServer()
