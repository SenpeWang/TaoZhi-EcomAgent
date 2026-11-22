"""MCP 协议规范、EcommerceMcpServer / McpClientAdapter 与领域工具边界测试。"""
from __future__ import annotations

import importlib
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from taozhi.core.tools.search_amazon import AMAZON_BASE_URL, search_amazon

_search_amazon_mod = importlib.import_module("taozhi.core.tools.search_amazon")
from taozhi.domain.research import classify_research_scope
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.protocol import McpCallToolResult
from taozhi.infrastructure.mcp.server import EcommerceMcpServer


def test_mcp_protocol_and_server_registration():
    """测试 MCP 服务端的工具发现（tools/list）与 Schema 契约规范。"""
    server = EcommerceMcpServer()
    tools = server.list_tools()
    tool_names = [t.name for t in tools]

    assert "search_amazon" in tool_names
    assert "search_1688_wholesale" in tool_names
    assert "search_web" in tool_names
    assert "fetch_web_page" in tool_names
    clock_schema = next(t.inputSchema for t in tools if t.name == "get_current_time")
    assert set(clock_schema["properties"]) == {"timezone"}
    assert clock_schema["properties"]["timezone"]["default"] == "Asia/Shanghai"
    assert clock_schema["additionalProperties"] is False

    amz_tool = next(t for t in tools if t.name == "search_amazon")
    assert amz_tool.inputSchema["type"] == "object"
    assert "query" in amz_tool.inputSchema["properties"]
    assert "query" in amz_tool.inputSchema["required"]


def test_current_time_through_mcp_uses_the_system_clock_and_default_timezone():
    adapter = McpClientAdapter()
    before = datetime.now(timezone.utc).replace(microsecond=0)
    outcome = adapter.call_tool("get_current_time", {}, remaining_seconds=2)
    after = datetime.now(timezone.utc)
    assert outcome.status == "ok" and outcome.dispatched and outcome.data_count == 1
    item, = outcome.items
    assert item["timezone"] == "Asia/Shanghai"
    current_time = datetime.fromisoformat(item["current_time"])
    assert before <= current_time <= after
    assert current_time.utcoffset() == timedelta(hours=8)
    assert item["current_time"] == current_time.isoformat(timespec="seconds")


@pytest.mark.parametrize(("month", "offset"), [(1, "-05:00"), (7, "-04:00")])
def test_current_time_observes_the_requested_timezone(month, offset):
    instant = datetime(2026, month, 1, 12, tzinfo=timezone.utc)
    with patch("taozhi.core.tools.current_time.datetime") as clock:
        clock.now.side_effect = instant.astimezone
        result = EcommerceMcpServer().call_tool("get_current_time", {"timezone": "America/New_York"})
    assert not result.isError
    item = json.loads(result.content[0].text)
    assert item["timezone"] == "America/New_York"
    assert item["current_time"].endswith(offset)
    assert datetime.fromisoformat(item["current_time"]) == instant


@pytest.mark.parametrize("zone", ["Unknown/City", "", None])
def test_current_time_rejects_invalid_timezones(zone):
    result = EcommerceMcpServer().call_tool("get_current_time", {"timezone": zone})
    assert result.isError and "无法识别时区" in result.content[0].text


def test_search_amazon_execution_and_data_structure():
    """测试 search_amazon 工具对 Amazon 搜索页 HTML DOM 的真实解析与结构契约。"""
    fake_html = (
        '<div data-component-type="s-search-result" data-asin="B0REAL0001">'
        '<h2><span>iPhone 16 Magnetic Case</span></h2>'
        '<span class="a-offscreen">$15.99</span>'
        '<span class="a-icon-alt">4.6 out of 5 stars</span>'
        '<span class="a-size-base s-underline-text">1,240</span>'
        '<span class="a-icon-prime"></span></div>'
    )
    fake_resp = MagicMock(status_code=200, text=fake_html)
    with patch.object(_search_amazon_mod.httpx, "Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.get.return_value = fake_resp
        results = search_amazon("iphone 16 case", limit=3)

    assert isinstance(results, list)
    assert len(results) == 1

    item = results[0]
    assert item["title"] == "iPhone 16 Magnetic Case"
    assert item["price"] == "$15.99"
    assert item["asin"] == "B0REAL0001"
    assert item["reviews_count"] == 1240
    assert item["source"] == "amazon"
    assert item["url"] == f"{AMAZON_BASE_URL}dp/B0REAL0001"


def test_mcp_server_call_tool_and_alias():
    """测试 MCP 服务端 tools/call 执行、规范命名及未知工具报错。"""
    server = EcommerceMcpServer()

    with patch(
        "taozhi.core.tools.search_amazon.search_amazon",
        return_value=[{"title": "Thermal Mug", "price": "$22.50", "asin": "B0MUG00001", "url": f"{AMAZON_BASE_URL}dp/B0MUG00001", "source": "amazon"}],
    ):
        res = server.call_tool("search_amazon", {"query": "thermal mug", "limit": 2})
    assert isinstance(res, McpCallToolResult)
    assert not res.isError
    assert len(res.content) == 1
    assert res.content[0].type == "text"

    parsed = json.loads(res.content[0].text)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["source"] == "amazon"

    with patch(
        "taozhi.core.tools.search_1688.search_1688_wholesale",
        return_value=[{"title": "东莞源头厂保温杯", "price": 18.5}],
    ):
        ws_res = server.call_tool("search_1688_wholesale", {"keyword": "保温杯", "limit": 2})
    assert not ws_res.isError
    ws_parsed = json.loads(ws_res.content[0].text)
    assert ws_parsed[0]["title"] == "东莞源头厂保温杯"
    assert server.call_tool("search_1688", {"keyword": "保温杯", "limit": 2}).isError

    err_res = server.call_tool("unknown_tool", {})
    assert err_res.isError
    assert "not found" in err_res.content[0].text


def test_mcp_client_adapter_integration():
    """测试 McpClientAdapter 的受限 Schema 发现、ToolOutcome 封装与取消中断。"""
    server = EcommerceMcpServer()
    adapter = McpClientAdapter(server=server)

    specs = adapter.list_tool_specs(["search_web", "fetch_web_page"])
    spec_names = [s["name"] for s in specs]
    assert spec_names == ["search_web", "fetch_web_page"]
    assert "inputSchema" in specs[0]

    with patch(
        "taozhi.core.tools.search_amazon.search_amazon",
        return_value=[{"title": "AMZ Mug", "price": "$19.99", "asin": "B001", "source": "amazon"}],
    ):
        outcome = adapter.call_tool("search_amazon", {"query": "screen protector", "limit": 2})
    assert outcome.status == "ok"
    assert outcome.data_count == 1
    assert outcome.items[0]["asin"] == "B001"

    cancel_evt = threading.Event()
    cancel_evt.set()
    cancelled = adapter.call_tool("search_amazon", {"query": "test"}, cancel_event=cancel_evt)
    assert cancelled.status == "cancelled"

    unknown = adapter.call_tool("non_existent_tool", {})
    assert unknown.status == "error"


def test_research_scope_filters_mcp_tool_specs():
    """测试领域范围守卫与 MCP tools/list 的白名单联动隔离。"""
    adapter = McpClientAdapter(server=EcommerceMcpServer())

    web_scope = classify_research_scope("调研无线耳机全网口碑与痛点")
    web_specs = adapter.list_tool_specs(web_scope.allowed_tools)
    web_names = {s["name"] for s in web_specs}
    assert web_names == {"search_web", "fetch_web_page", "get_current_time"}

    sourcing_scope = classify_research_scope("调研保温杯1688出厂价与Amazon零售对标")
    sourcing_specs = adapter.list_tool_specs(sourcing_scope.allowed_tools)
    sourcing_names = {s["name"] for s in sourcing_specs}
    assert "search_1688_wholesale" in sourcing_names
    assert "search_amazon" in sourcing_names
