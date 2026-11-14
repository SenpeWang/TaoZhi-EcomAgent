"""MCP 协议规范、EcommerceMcpServer / McpClientAdapter 与领域工具边界测试。"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

from taozhi.core.tools.search_amazon import AMAZON_BASE_URL, search_amazon
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

    amz_tool = next(t for t in tools if t.name == "search_amazon")
    assert amz_tool.inputSchema["type"] == "object"
    assert "query" in amz_tool.inputSchema["properties"]
    assert "query" in amz_tool.inputSchema["required"]


def test_search_amazon_execution_and_data_structure():
    """测试 search_amazon 工具的输出结构契约。"""
    results = search_amazon("iphone 16 case", limit=3)
    assert isinstance(results, list)
    assert len(results) >= 1

    item = results[0]
    assert "title" in item
    assert "price" in item
    assert "asin" in item
    assert "url" in item
    assert item["source"] == "amazon"
    assert item["url"].startswith(AMAZON_BASE_URL) or "amazon.com" in item["url"]


def test_mcp_server_call_tool_and_alias():
    """测试 MCP 服务端 tools/call 执行、别名兼容及未知工具报错。"""
    server = EcommerceMcpServer()

    res = server.call_tool("search_amazon", {"query": "thermal mug", "limit": 2})
    assert isinstance(res, McpCallToolResult)
    assert not res.isError
    assert len(res.content) == 1
    assert res.content[0].type == "text"

    parsed = json.loads(res.content[0].text)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    assert parsed[0]["source"] == "amazon"

    # 测试 search_1688 别名可映射到 canonical 工具 search_1688_wholesale
    with patch(
        "taozhi.infrastructure.mcp.server.search_1688_wholesale",
        return_value=[{"title": "东莞源头厂保温杯", "price": 18.5}],
    ):
        alias_res = server.call_tool("search_1688", {"keyword": "保温杯", "limit": 2})
    assert not alias_res.isError
    alias_parsed = json.loads(alias_res.content[0].text)
    assert alias_parsed[0]["title"] == "东莞源头厂保温杯"

    err_res = server.call_tool("unknown_tool", {})
    assert err_res.isError
    assert "not found" in err_res.content[0].text


def test_mcp_client_adapter_integration():
    """测试 McpClientAdapter 的受限 Schema 发现、ToolOutcome 封装与取消中断。"""
    server = EcommerceMcpServer()
    adapter = McpClientAdapter(server=server)

    # 1. 受限 tools/list 过滤
    specs = adapter.list_tool_specs(["search_web", "fetch_web_page"])
    spec_names = [s["name"] for s in specs]
    assert spec_names == ["search_web", "fetch_web_page"]
    assert "inputSchema" in specs[0]

    # 2. 通过 MCP tools/call 执行并返回结构化 ToolOutcome
    with patch(
        "taozhi.infrastructure.mcp.server.search_amazon",
        return_value=[{"title": "AMZ Mug", "price": "$19.99", "asin": "B001", "source": "amazon"}],
    ):
        outcome = adapter.call_tool("search_amazon", {"query": "screen protector", "limit": 2})
    assert outcome.status == "ok"
    assert outcome.data_count == 1
    assert outcome.items[0]["asin"] == "B001"

    # 3. 任务取消事件拦截
    cancel_evt = threading.Event()
    cancel_evt.set()
    cancelled = adapter.call_tool("search_amazon", {"query": "test"}, cancel_event=cancel_evt)
    assert cancelled.status == "cancelled"

    # 4. 未注册工具映射为错误态 ToolOutcome
    unknown = adapter.call_tool("non_existent_tool", {})
    assert unknown.status == "error"


def test_research_scope_filters_mcp_tool_specs():
    """测试领域范围守卫与 MCP tools/list 的白名单联动隔离。"""
    adapter = McpClientAdapter(server=EcommerceMcpServer())

    web_scope = classify_research_scope("调研无线耳机全网口碑与痛点")
    web_specs = adapter.list_tool_specs(web_scope.allowed_tools)
    web_names = {s["name"] for s in web_specs}
    assert web_names == {"search_web", "fetch_web_page"}

    sourcing_scope = classify_research_scope("调研保温杯1688出厂价与Amazon零售对标")
    sourcing_specs = adapter.list_tool_specs(sourcing_scope.allowed_tools)
    sourcing_names = {s["name"] for s in sourcing_specs}
    assert "search_1688_wholesale" in sourcing_names
    assert "search_amazon" in sourcing_names
