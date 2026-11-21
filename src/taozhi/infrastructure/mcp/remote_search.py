"""公开 MCP 检索服务客户端：以 Streamable HTTP 调用远端 web_search / web_fetch 工具。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

import httpx

from taozhi.observability.tracing import record_diagnostic

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_RESULT_PATTERN = re.compile(
    r"^Title:\s*(?P<title>.+?)\nURL:\s*(?P<url>\S+)\n"
    r"(?:Published:[^\n]*\n)?(?:Author:[^\n]*\n)?"
    r"(?:Highlights:\n)?(?P<body>.*?)(?=^Title:|\Z)",
    re.MULTILINE | re.DOTALL,
)
_MAX_SNIPPET = 600


def endpoint() -> str:
    """读取远端 MCP 检索服务地址（未配置时返回空串）。

    Returns:
        环境变量 ``ECOM_MCP_SEARCH_URL`` 的取值，空串表示未启用远端检索。
    """
    return os.environ.get("ECOM_MCP_SEARCH_URL", "").strip()


def _decode(resp: httpx.Response) -> Dict[str, Any]:
    """解析 JSON 或 SSE 形式的 JSON-RPC 响应。

    Args:
        resp: 远端 MCP 服务返回的 HTTP 响应。

    Returns:
        解析后的 JSON-RPC 报文；无法解析时返回空字典。
    """
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        payloads = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        if not payloads:
            return {}
        return json.loads(payloads[-1])
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return {}


def _rpc(client: httpx.Client, url: str, method: str, params: Dict[str, Any], session: str | None, rid: int) -> Dict[str, Any]:
    """向远端 MCP 服务发送一次 JSON-RPC 请求。

    Args:
        client: 复用的 HTTP 客户端。
        url: MCP 服务端点。
        method: JSON-RPC 方法名。
        params: 方法参数。
        session: 会话标识，首次初始化时为空。
        rid: 请求编号。

    Returns:
        解析后的 JSON-RPC 响应报文。
    """
    headers = dict(_HEADERS)
    if session:
        headers["Mcp-Session-Id"] = session
    resp = client.post(url, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, headers=headers)
    resp.raise_for_status()
    return _decode(resp)


def _open(client: httpx.Client, url: str) -> str | None:
    """完成 MCP 初始化并返回会话标识。

    Args:
        client: 复用的 HTTP 客户端。
        url: MCP 服务端点。

    Returns:
        服务端分配的会话标识，未分配时返回 None。
    """
    headers = dict(_HEADERS)
    resp = client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "taozhi-agent", "version": "1.0"},
            },
        },
        headers=headers,
    )
    resp.raise_for_status()
    return resp.headers.get("mcp-session-id")


def _call_tool(url: str, tool: str, arguments: Dict[str, Any], timeout: float) -> List[Dict[str, Any]]:
    """调用远端 MCP 工具并返回内容块文本。

    Args:
        url: MCP 服务端点。
        tool: 工具名称。
        arguments: 工具参数。
        timeout: 请求超时秒数。

    Returns:
        内容块文本列表；调用失败返回空列表。
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        session = _open(client, url)
        body = _rpc(
            client,
            url,
            "tools/call",
            {"name": tool, "arguments": arguments},
            session,
            2,
        )
    result = body.get("result") or {}
    if result.get("isError"):
        return []
    blocks = result.get("content") or []
    texts = [block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("text")]
    return texts


def parse_items(text: str, limit: int, source: str) -> List[Dict[str, Any]]:
    """把远端检索返回的 markdown 结果解析为结构化条目。

    Args:
        text: 远端工具返回的文本。
        limit: 最多返回条数。
        source: 数据来源标记。

    Returns:
        含 ``title``、``snippet``、``url``、``source`` 的条目列表。
    """
    items: List[Dict[str, Any]] = []
    for match in _RESULT_PATTERN.finditer(text or ""):
        title = match.group("title").strip()
        url = match.group("url").strip()
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        if not title or not url.startswith("http"):
            continue
        items.append(
            {
                "title": title,
                "snippet": body[:_MAX_SNIPPET],
                "url": url,
                "source": source,
            }
        )
        if len(items) >= limit:
            break
    return items


def search_web_via_mcp(query: str, *, limit: int = 10, timeout: float = 25.0) -> List[Dict[str, Any]]:
    """通过公开 MCP 服务执行网页检索。

    Args:
        query: 检索词条。
        limit: 返回条数上限。
        timeout: 请求超时秒数。

    Returns:
        结构化结果列表；未配置服务或调用失败时返回空列表。
    """
    url = endpoint()
    if not url:
        return []
    try:
        texts = _call_tool(url, "web_search_exa", {"query": query, "numResults": limit}, timeout)
    except Exception as exc:
        record_diagnostic("tool.mcp_search", "fallback", exc, publish_now=False)
        return []
    if not texts:
        record_diagnostic("tool.mcp_search", "fallback", None, publish_now=False)
        return []
    return parse_items(texts[0], limit, "exa-mcp")


def fetch_page_via_mcp(url: str, *, timeout: float = 25.0) -> str:
    """通过公开 MCP 服务抓取网页正文。

    Args:
        url: 目标网页地址。
        timeout: 请求超时秒数。

    Returns:
        正文 Markdown 文本；未配置或失败时返回空串。
    """
    service = endpoint()
    if not service:
        return ""
    try:
        texts = _call_tool(service, "web_fetch_exa", {"urls": [url]}, timeout)
    except Exception as exc:
        record_diagnostic("tool.mcp_fetch", "fallback", exc, publish_now=False)
        return ""
    return texts[0] if texts else ""