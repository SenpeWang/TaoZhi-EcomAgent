"""提供调研 Agent 的 MCP 工具发现与调用。"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

import httpx

from taozhi.core.tools import current_time, search_1688, search_amazon, search_web, web_scraper
from taozhi.core.tools.outcome import ToolInvocationError
from taozhi.domain.trace_diagnostic import CATALOG
from taozhi.observability.tracing import record_diagnostic
from .protocol import McpCallToolResult, McpContentItem, McpToolDefinition
from .schema import apply_defaults, validate_arguments


_SEARCH_TOOL_MODULES = {
    "search_amazon": search_amazon,
    "search_1688_wholesale": search_1688,
    "search_web": search_web,
    "fetch_web_page": web_scraper,
}

_OUTPUT_WEB = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "snippet": {"type": ["string", "null"]},
            "source": {"type": "string"},
        },
        "required": ["title", "url", "snippet", "source"],
        "additionalProperties": False,
    },
}

_OUTPUT_AMAZON = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "asin": {"type": ["string", "null"]},
            "price_text": {"type": ["string", "null"]},
            "price_value": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "rating_value": {"type": ["number", "null"]},
            "reviews_count": {"type": ["integer", "null"]},
            "is_prime": {"type": ["boolean", "null"]},
            "price_source": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": [
            "title", "url", "asin", "price_text", "price_value", "currency",
            "rating_value", "reviews_count", "is_prime", "price_source", "source",
        ],
        "additionalProperties": False,
    },
}

_OUTPUT_1688 = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "price_text": {"type": ["string", "null"]},
            "price_value": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "moq": {"type": ["integer", "null"]},
            "shop": {"type": ["string", "null"]},
            "sales": {"type": ["integer", "null"]},
            "source": {"type": "string"},
        },
        "required": [
            "title", "url", "price_text", "price_value", "currency",
            "moq", "shop", "sales", "source",
        ],
        "additionalProperties": False,
    },
}

_OUTPUT_FETCH = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "status": {"type": "string", "enum": ["ok", "error"]},
        "title": {"type": "string"},
        "text": {"type": "string"},
        "truncated": {"type": "boolean"},
        "status_code": {"type": ["integer", "null"]},
        "error": {"type": ["string", "null"]},
        "error_code": {"type": ["string", "null"]},
    },
    "required": [
        "url", "status", "title", "text", "truncated", "status_code", "error", "error_code",
    ],
    "additionalProperties": False,
}

_OUTPUT_CLOCK = {
    "type": "object",
    "properties": {
        "current_time": {"type": "string"},
        "timezone": {"type": "string"},
    },
    "required": ["current_time", "timezone"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMAS = {
    "search_amazon": _OUTPUT_AMAZON,
    "search_1688_wholesale": _OUTPUT_1688,
    "search_web": _OUTPUT_WEB,
    "fetch_web_page": _OUTPUT_FETCH,
}


def _build_search_handler(tool_name: str) -> Callable[[dict[str, Any]], Any]:
    """按已校验的参数调用对应采集器。"""
    module = _SEARCH_TOOL_MODULES[tool_name]

    def handle(arguments: dict[str, Any]) -> Any:
        """调用预绑定采集器的同名工具函数。

        Args:
            arguments: 已校验的工具参数。

        Returns:
            采集器原始返回值。
        """
        return getattr(module, tool_name)(**arguments)

    return handle


class EcommerceMcpServer:
    """电商与市场情报类 MCP 服务端（支持 tools/list 与 tools/call）。"""

    _SEARCH_TOOL_SPECS: tuple[tuple[str, str, dict[str, Any]], ...] = (
        (
            "search_amazon",
            "检索 Amazon 商品的美元售价、评分、评论数与 ASIN；依次尝试外部网关、Amazon 搜索页、"
            "DuckDuckGo 定向检索；无真实结果时返回空列表，不编造数据。"
            "输出字段：title、url、asin、price_text、price_value、currency、rating_value、"
            "reviews_count、is_prime、price_source、source；未知值一律为 null，"
            "price_source 区分商品页报价与搜索摘要报价。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "英文或中文检索关键词（例如 iphone 16 case）"},
                    "limit": {
                        "type": "integer",
                        "description": "返回商品条数上限",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "单次请求超时时间（秒）",
                        "default": 12.0,
                        "minimum": 1,
                        "maximum": 120,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        (
            "search_1688_wholesale",
            "检索 1688 批发渠道的源头工厂报价、起订量（MOQ）与供应商分布；优先外部网关，"
            "其次本地 Playwright 采集；无匹配时返回空列表。"
            "输出字段：title、url、price_text、price_value、currency、moq、shop、sales、source；"
            "缺 url 的条目会被丢弃，未知值为 null。",
            {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "1688 检索关键词"},
                    "limit": {
                        "type": "integer",
                        "description": "返回条目上限",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "单次请求超时时间（秒）",
                        "default": 90.0,
                        "minimum": 1,
                        "maximum": 120,
                    },
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        ),
        (
            "search_web",
            "检索公开网页的行业趋势、卖点与用户评价；依次尝试远端 MCP、外部检索网关、"
            "DuckDuckGo、Bing；无匹配时返回空列表。"
            "输出字段：title、url、snippet、source；snippet 缺失为 null，source 标明实际通道。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或调研主题"},
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "单次请求超时时间（秒）",
                        "default": 12.0,
                        "minimum": 1,
                        "maximum": 120,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        (
            "fetch_web_page",
            "抓取公网 HTTP/HTTPS 网页并提取正文；逐跳校验并固定解析 IP，仅接受文本响应且"
            "响应体上限 2 MiB；返回 url、status、title、text、truncated、status_code、error、"
            "error_code；失败时 text 为空并在 error 给出中文原因，正文被 max_chars 截断时 "
            "truncated 为 true。",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 URL（仅公网 HTTP/HTTPS）"},
                    "max_chars": {
                        "type": "integer",
                        "description": "最多截取的纯文字数",
                        "default": 4000,
                        "minimum": 200,
                        "maximum": 20000,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "单跳请求超时时间（秒）",
                        "default": 10.0,
                        "minimum": 1,
                        "maximum": 60,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
    )

    def __init__(self) -> None:
        self._tools: Dict[str, McpToolDefinition] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册内置检索、网页抓取与当前时间工具。"""
        for name, desc, schema in self._SEARCH_TOOL_SPECS:
            self.register_tool(
                McpToolDefinition(
                    name=name,
                    description=desc,
                    inputSchema=schema,
                    outputSchema=_OUTPUT_SCHEMAS[name],
                ),
                handler=_build_search_handler(name),
            )
        self.register_tool(
            McpToolDefinition(
                name="get_current_time",
                description="读取当前日期和时间，可指定 IANA 时区；输出 current_time（ISO 8601，含 UTC 偏移）与 timezone 两个字段。",
                outputSchema=_OUTPUT_CLOCK,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "default": current_time.DEFAULT_TIMEZONE,
                            "description": "IANA 时区名称，如 Asia/Shanghai 或 UTC",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            handler=lambda args: current_time.get_current_time(**args),
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
            definition = self._tools[name]
            call_arguments = apply_defaults(definition.inputSchema, arguments)
            validate_arguments(definition.inputSchema, call_arguments)
            raw_output = self._handlers[name](call_arguments)
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
            if isinstance(exc, ToolInvocationError):
                code = exc.code
            elif isinstance(exc, ValueError):
                code = "tool.invalid_arguments"
            else:
                code = "tool.timeout" if timed_out else "tool.error"
            record_diagnostic(code, "failed", exc)
            return McpCallToolResult(
                content=[McpContentItem(type="text", text=CATALOG[code][1])],
                isError=True,
            )


default_mcp_server = EcommerceMcpServer()
