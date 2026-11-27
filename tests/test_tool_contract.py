"""MCP 工具描述、参数 schema 与实现签名的一致性契约。"""
from __future__ import annotations

import inspect

import pytest

from taozhi.application.research.subagents import _step_arguments
from taozhi.application.research.schemas import _SubagentStepSchema
from taozhi.core.tools import current_time, search_1688, search_amazon, search_web, web_scraper
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.protocol import McpToolDefinition
from taozhi.infrastructure.mcp.server import EcommerceMcpServer


TOOL_FUNCTIONS = {
    "search_amazon": search_amazon.search_amazon,
    "search_1688_wholesale": search_1688.search_1688_wholesale,
    "search_web": search_web.search_web,
    "fetch_web_page": web_scraper.fetch_web_page,
    "get_current_time": current_time.get_current_time,
}

DESCRIPTION_KEYWORDS = {
    "search_web": ("MCP", "DuckDuckGo", "Bing", "空列表", "title", "url", "snippet", "source"),
    "search_1688_wholesale": (
        "1688", "网关", "空列表", "title", "url", "price_text", "moq", "shop", "sales",
    ),
    "search_amazon": (
        "Amazon", "ASIN", "DuckDuckGo", "空列表", "price_text", "price_value",
        "rating_value", "price_source",
    ),
    "fetch_web_page": ("公网", "正文", "2 MiB", "truncated", "status_code", "error_code"),
    "get_current_time": ("IANA", "时区", "current_time", "timezone"),
}


def _tools():
    """返回 MCP 服务器暴露的工具映射。"""
    return {tool.name: tool for tool in EcommerceMcpServer().list_tools()}


def test_every_tool_description_covers_real_behavior():
    """工具描述必须覆盖真实降级链、返回形态或安全限制。"""
    for name, keywords in DESCRIPTION_KEYWORDS.items():
        description = _tools()[name].description
        assert description.strip()
        for keyword in keywords:
            assert keyword in description, (name, keyword, description)


def test_every_tool_has_closed_schema():
    """工具参数必须是闭合对象，避免模型传入未声明字段。"""
    for name, tool in _tools().items():
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]


def test_schema_parameters_and_defaults_match_function_signature():
    """schema 的属性名、必填项和默认值必须与真实实现签名一致。"""
    for name, tool in _tools().items():
        schema = tool.inputSchema
        signature = inspect.signature(TOOL_FUNCTIONS[name])
        properties = schema["properties"]
        assert set(properties) == set(signature.parameters), name
        required = {
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
        }
        assert set(schema.get("required") or ()) == required, name
        for property_name, specification in properties.items():
            if "default" not in specification:
                continue
            assert signature.parameters[property_name].default == specification["default"], (name, property_name)


def test_numeric_parameters_reject_out_of_range_values():
    """limit/timeout/max_chars 的边界必须由服务端统一拦截。"""
    server = EcommerceMcpServer()
    for arguments in (
        {"query": "case", "limit": 0},
        {"query": "case", "limit": 51},
        {"query": "case", "timeout": 0},
        {"query": "case", "timeout": 121},
    ):
        assert server.call_tool("search_web", arguments).isError
    assert server.call_tool("fetch_web_page", {"url": "https://example.com", "max_chars": 100}).isError
    assert server.call_tool("fetch_web_page", {"url": "https://example.com", "max_chars": 30000}).isError


def test_adapter_and_server_apply_schema_defaults_before_handler():
    """服务端与适配器按 schema 补齐默认值；适配器只下调超时，不发明 30 秒。"""
    server = EcommerceMcpServer()
    captured: dict = {}

    def handler(arguments):
        """记录参数并返回空结果的工具处理器桩。

        Args:
            arguments: 工具调用参数。
        """
        captured.clear()
        captured.update(arguments)
        return []

    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            "timeout": {"type": "number", "default": 12.0, "minimum": 1, "maximum": 120},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    server.register_tool(McpToolDefinition(name="capture_tool", description="测试工具", inputSchema=schema), handler)

    server.call_tool("capture_tool", {"query": "x"})
    assert captured == {"query": "x", "limit": 10, "timeout": 12.0}

    adapter = McpClientAdapter(server=server)
    adapter.call_tool("capture_tool", {"query": "x"}, remaining_seconds=450)
    assert captured["timeout"] == 12.0

    adapter.call_tool("capture_tool", {"query": "x"}, remaining_seconds=5)
    assert captured["timeout"] == 5.0


def test_subagent_step_arguments_defer_to_schema_defaults():
    """子智能体只传检索词/URL，条数与超时由工具 schema 决定。"""
    web = _SubagentStepSchema(action="call_tool", thought="查", tool_name="search_web", query_or_url="手机壳")
    amazon = _SubagentStepSchema(action="call_tool", thought="查", tool_name="search_amazon", query_or_url="case")
    sourcing = _SubagentStepSchema(action="call_tool", thought="查", tool_name="search_1688_wholesale", query_or_url="保温杯")
    page = _SubagentStepSchema(action="call_tool", thought="抓", tool_name="fetch_web_page", query_or_url="https://example.com")
    clock = _SubagentStepSchema(action="call_tool", thought="时间", tool_name="get_current_time", timezone="UTC")

    assert _step_arguments("search_web", web, "seed") == {"query": "手机壳"}
    assert _step_arguments("search_amazon", amazon, "seed") == {"query": "case"}
    assert _step_arguments("search_1688_wholesale", sourcing, "seed") == {"keyword": "保温杯"}
    assert _step_arguments("fetch_web_page", page, "seed") == {"url": "https://example.com"}
    assert _step_arguments("get_current_time", clock, "seed") == {"timezone": "UTC"}


def test_every_tool_declares_closed_output_schema():
    """每个工具都必须声明闭合的输出 schema，供模型与测试共用。"""
    for name, tool in _tools().items():
        schema = tool.outputSchema
        assert schema is not None, name
        node = schema["items"] if schema.get("type") == "array" else schema
        assert node["additionalProperties"] is False, name
        assert node["properties"], name
        assert set(node["required"]) == set(node["properties"]), name


def test_output_schema_matches_real_normalized_fields(monkeypatch):
    """输出 schema 的字段必须与真实归一化结果逐字段一致。"""
    tools = _tools()
    web_items = search_web._normalize_items(
        [{"title": "标题", "url": "https://example.com/a", "snippet": "", "source": "duckduckgo"}],
        5,
    )
    monkeypatch.setattr(
        search_amazon,
        "search_duckduckgo_html",
        lambda *_args, **_kwargs: [
            {
                "title": "Case",
                "snippet": "Protective cover $9.99",
                "url": "https://www.amazon.com/dp/B0REAL1234",
                "source": "duckduckgo",
            }
        ],
    )
    amazon_items = search_amazon._fallback_duckduckgo_amazon_search("case", limit=5)
    sourcing_items = search_1688._normalize_items(
        {"items": [{"title": "源头厂", "url": "detail.1688.com/offer/1.html", "price": "18.5", "moq": 2}]},
        5,
        source="1688-gateway",
    )

    for name, items in {
        "search_web": web_items,
        "search_amazon": amazon_items,
        "search_1688_wholesale": sourcing_items,
    }.items():
        assert items, name
        expected = set(tools[name].outputSchema["items"]["properties"])
        for item in items:
            assert set(item) == expected, (name, sorted(item))

    fetch_item = web_scraper._error("https://example.com", "失败", "CODE")
    assert set(fetch_item) == set(tools["fetch_web_page"].outputSchema["properties"])
    clock_item = current_time.get_current_time("UTC")
    assert set(clock_item) == set(tools["get_current_time"].outputSchema["properties"])


def test_amazon_output_drops_legacy_dual_fields():
    """Amazon 输出不得同时保留字符串与数值两份评分/价格字段。"""
    schema = _tools()["search_amazon"].outputSchema["items"]["properties"]
    assert "rating" not in schema and "rating_score" not in schema
    assert "price" not in schema
    assert {"price_text", "price_value", "currency", "rating_value", "price_source"} <= set(schema)
