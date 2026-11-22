"""公开 MCP 检索客户端与搜索降级链路的单元测试。"""
from __future__ import annotations

import importlib
import json

import httpx

from taozhi.infrastructure.mcp import remote_search as rs


class _Resp:
    def __init__(self, status_code=200, text="", headers=None, payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, url, json=None, headers=None):
        return self._responses.pop(0)


_SAMPLE = """Title: 手机支架选购指南
URL: https://example.com/a
Published: 2026-01-01T00:00:00Z
Author: N/A
Highlights:
正文 A 内容

Title: 第二篇
URL: https://example.com/b
Highlights:
正文 B 内容
"""


def test_parse_items_extracts_structured_results():
    """远端 markdown 文本被解析为标题、链接与摘要。"""
    items = rs.parse_items(_SAMPLE, limit=5, source="exa-mcp")
    assert [item["url"] for item in items] == ["https://example.com/a", "https://example.com/b"]
    assert items[0]["title"] == "手机支架选购指南"
    assert "正文 A" in items[0]["snippet"]
    assert all(item["source"] == "exa-mcp" for item in items)


def test_search_web_via_mcp_returns_empty_without_endpoint(monkeypatch):
    """未配置远端地址时直接返回空列表，不发起网络请求。"""
    monkeypatch.delenv("ECOM_MCP_SEARCH_URL", raising=False)
    assert rs.search_web_via_mcp("手机支架") == []


def test_search_web_via_mcp_parses_session_and_content(monkeypatch):
    """初始化拿会话、调用工具并解析 SSE 响应。"""
    monkeypatch.setenv("ECOM_MCP_SEARCH_URL", "https://mcp.test/mcp")
    sse = "event: message\ndata: " + json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": _SAMPLE}]}}
    ) + "\n"
    responses = [
        _Resp(headers={"mcp-session-id": "sess-1"}),
        _Resp(headers={"content-type": "text/event-stream"}, text=sse),
    ]
    monkeypatch.setattr(rs.httpx, "Client", lambda **_kw: _Client(responses))
    items = rs.search_web_via_mcp("手机支架", limit=5)
    assert len(items) == 2
    assert items[1]["url"] == "https://example.com/b"


def test_search_web_via_mcp_handles_error_result(monkeypatch):
    """远端返回 isError 时降级为空列表而不是抛异常。"""
    monkeypatch.setenv("ECOM_MCP_SEARCH_URL", "https://mcp.test/mcp")
    sse = "event: message\ndata: " + json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": []}}
    ) + "\n"
    responses = [_Resp(headers={"mcp-session-id": "s1"}), _Resp(text=sse, headers={"content-type": "text/event-stream"})]
    monkeypatch.setattr(rs.httpx, "Client", lambda **_kw: _Client(responses))
    assert rs.search_web_via_mcp("手机支架") == []


def test_duckduckgo_anti_bot_returns_empty(monkeypatch):
    """DuckDuckGo 返回 202 反爬页时返回空列表，不再抛 ValueError。"""
    sw = importlib.import_module("taozhi.core.tools.search_web")

    class _AntiBot(_Client):
        def post(self, url, data=None, **_kw):
            return _Resp(status_code=202, text="<html>anomaly</html>")

    monkeypatch.setattr(sw.httpx, "Client", lambda **_kw: _AntiBot([]))
    assert sw.search_duckduckgo_html("手机支架") == []


def test_search_web_prefers_mcp_results(monkeypatch):
    """MCP 返回结果时直接采用，不再调用后续通道。"""
    sw = importlib.import_module("taozhi.core.tools.search_web")

    monkeypatch.setattr(sw, "search_web_via_mcp", lambda *_a, **_k: [{"title": "t", "snippet": "s", "url": "https://x", "source": "exa-mcp"}])
    items = sw.search_web("手机支架", limit=3)
    assert items and items[0]["source"] == "exa-mcp"