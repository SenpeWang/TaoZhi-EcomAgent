"""MCP 协议规范与 search_amazon 跨境情报工具验收测试。"""
from __future__ import annotations

import json
import pytest

from taozhi.agent.tools.base import ToolContext
from taozhi.agents.registry import default_registry
from taozhi.core.tools.search_amazon import AMAZON_BASE_URL, search_amazon
from taozhi.infrastructure.mcp.adapter import McpToolAdapter
from taozhi.infrastructure.mcp.protocol import McpCallToolResult, McpToolDefinition
from taozhi.infrastructure.mcp.server import EcommerceMcpServer, default_mcp_server
from taozhi.observability.tracer import active_trace


def test_mcp_protocol_and_server_registration():
    """测试 MCP 服务端的工具发现（tools/list）与 Schema 契约规范。"""
    server = EcommerceMcpServer()
    tools = server.list_tools()
    tool_names = [t.name for t in tools]

    assert "search_amazon" in tool_names
    assert "search_1688" in tool_names
    assert "search_web" in tool_names
    assert "fetch_web_page" in tool_names

    # 验证 search_amazon 的 inputSchema 符合 JSON Schema 规范
    amz_tool = next(t for t in tools if t.name == "search_amazon")
    assert amz_tool.inputSchema["type"] == "object"
    assert "query" in amz_tool.inputSchema["properties"]
    assert "query" in amz_tool.inputSchema["required"]


def test_search_amazon_execution_and_data_structure():
    """测试 search_amazon 工具的真实或容灾抓取输出结构。"""
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


def test_mcp_server_call_tool():
    """测试 MCP 服务端的工具分发与执行（tools/call）契约。"""
    server = EcommerceMcpServer()

    # 1. 正常调用 search_amazon
    res = server.call_tool("search_amazon", {"query": "thermal mug", "limit": 2})
    assert isinstance(res, McpCallToolResult)
    assert not res.isError
    assert len(res.content) == 1
    assert res.content[0].type == "text"

    parsed = json.loads(res.content[0].text)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    assert parsed[0]["source"] == "amazon"

    # 2. 调用不存在的未知工具
    err_res = server.call_tool("unknown_tool", {})
    assert err_res.isError
    assert "not found" in err_res.content[0].text


def test_mcp_tool_adapter_integration():
    """测试 McpToolAdapter 将 MCP 工具转接为 BaseTool 原生契约。"""
    server = EcommerceMcpServer()
    amz_def = next(t for t in server.list_tools() if t.name == "search_amazon")
    adapter = McpToolAdapter(tool_definition=amz_def, server=server)

    assert adapter.name == "search_amazon"

    # 1. 测试 dry-run 演练模式
    dry_res = adapter({"query": "test"}, context=ToolContext(dry_run=True))
    assert dry_res.success
    assert dry_res.output["status"] == "dry_run_validated"

    # 2. 测试正常调用与 Trace 树集成
    with active_trace() as trace:
        normal_res = adapter({"query": "screen protector", "limit": 2})

    assert normal_res.success
    assert isinstance(normal_res.output, list)
    record = trace.finish()

    # 验证 Trace 树中包含 mcp.tool.search_amazon Span
    span_names = [c.name for c in record.root_span.children]
    assert "mcp.tool.search_amazon" in span_names


def test_agent_registry_equipped_with_amazon():
    """测试专职智能体角色工具边界（scanner 装备 1688 与 Amazon，web 仅搜索网页）。"""
    researcher = default_registry.get("web_agent")
    scanner = default_registry.get("scanner_agent")

    assert researcher is not None
    assert scanner is not None

    researcher_tool_names = [t.__name__ for t in researcher.tools]
    scanner_tool_names = [t.__name__ for t in scanner.tools]

    # 全网市场情报分析专员仅搜索网页，不包含 search_amazon
    assert "search_web" in researcher_tool_names
    assert "search_amazon" not in researcher_tool_names
    assert len(researcher.tools) == 1

    # 货源比价专员装备 1688 与 search_amazon 进行供应链比价对标（共 2 个工具）
    assert "search_1688_wholesale" in scanner_tool_names
    assert "search_amazon" in scanner_tool_names
    assert len(scanner.tools) == 2