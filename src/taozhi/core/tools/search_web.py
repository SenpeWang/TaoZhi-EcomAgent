"""全网网页实时搜索工具（MCP / 外部网关 / DuckDuckGo / Bing 多级降级）。

提取标题、核心摘要与真实网页链接供多智能体开展竞品舆情、市场趋势与差评洞察分析。
"""
from __future__ import annotations

import base64
import html
import os
import re
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from taozhi.observability.tracing import record_diagnostic
from .outcome import dedupe_items, is_http_url, raise_if_cancelled, result_items
from taozhi.infrastructure.mcp.remote_search import search_web_via_mcp

_DDG_RESULT_PATTERN = re.compile(
    r'<a class="result__url"[^>]*href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>.*?'
    r'<a[^>]*>(?P<title>.*?)</a>.*?<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)

_ANTI_BOT_STATUS = frozenset({202, 403, 429})
_BING_BLOCK_PATTERN = re.compile(r'<li class="b_algo".*?</li>', re.DOTALL)
_BING_LINK_PATTERN = re.compile(r'<h2[^>]*>\s*<a[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.DOTALL)
_BING_SNIPPET_PATTERN = re.compile(r"<p[^>]*>(?P<snippet>.*?)</p>", re.DOTALL)


def _normalize_items(
    items: List[Dict[str, Any]],
    limit: int,
    *,
    default_source: str = "web",
) -> List[Dict[str, Any]]:
    """只保留带标题与公网链接的条目，统一字段并去重后截断。

    Args:
        items: 通道返回的原始条目。
        limit: 返回条数上限。
        default_source: 条目未标注来源时使用的通道名称。

    Returns:
        固定包含 ``title``、``url``、``snippet``、``source`` 的条目列表；
        无法核对的条目被丢弃，缺失摘要记为 None。
    """
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not is_http_url(url):
            continue
        snippet = str(item.get("snippet") or "").strip()
        cleaned.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet or None,
                "source": str(item.get("source") or "").strip() or default_source,
            }
        )
    return dedupe_items(cleaned)[:limit]


def search_duckduckgo_html(
    query: str,
    *,
    limit: int = 10,
    timeout: float = 12.0,
    source: str = "duckduckgo",
) -> List[Dict[str, Any]]:
    """通过 DuckDuckGo HTML 端点执行免密钥全网检索并解析跳转链接。

    Args:
        query: 检索词条。
        limit: 返回结果条数上限。
        timeout: HTTP 请求超时秒数。
        source: 结果条目中标记的数据来源字段。

    Returns:
        结构化检索结果列表，成功查询无结果时返回空列表。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    results: List[Dict[str, Any]] = []
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        resp = client.post("https://html.duckduckgo.com/html/", data={"q": query})
        if resp.status_code in _ANTI_BOT_STATUS:
            record_diagnostic("tool.search_blocked", "fallback", None)
            return []
        resp.raise_for_status()
        for match in _DDG_RESULT_PATTERN.finditer(resp.text):
            clean_title = html.unescape(re.sub(r"<[^<]+?>", "", match.group("title")).strip())
            clean_snippet = html.unescape(re.sub(r"<[^<]+?>", "", match.group("snippet")).strip())
            raw_url = match.group("url")
            actual_url = raw_url
            if "uddg=" in raw_url:
                url_match = re.search(r"uddg=([^&]+)", raw_url)
                if url_match:
                    actual_url = unquote(url_match.group(1))
            if clean_title and clean_snippet:
                results.append(
                    {
                        "title": clean_title,
                        "snippet": clean_snippet,
                        "url": actual_url,
                        "source": source,
                    }
                )
            if len(results) >= limit:
                break
    return results


def _resolve_bing_url(url: str) -> str:
    """还原 Bing 跳转壳链接中的真实目标地址。"""
    raw = html.unescape(url)
    parsed = urlparse(raw)
    if "/ck/a" not in parsed.path:
        return raw
    encoded = (parse_qs(parsed.query).get("u") or [""])[0]
    if not encoded:
        return raw
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "ignore")
    except Exception:
        return raw
    return decoded if decoded.startswith("http") else raw


def search_bing_html(
    query: str,
    *,
    limit: int = 10,
    timeout: float = 12.0,
    source: str = "bing",
) -> List[Dict[str, Any]]:
    """通过 Bing HTML 端点执行备用全网检索。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    results: List[Dict[str, Any]] = []
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        resp = client.get("https://www.bing.com/search", params={"q": query, "count": max(limit, 10)})
        if resp.status_code in _ANTI_BOT_STATUS:
            record_diagnostic("tool.search_blocked", "fallback", None)
            return []
        resp.raise_for_status()
        for block in _BING_BLOCK_PATTERN.findall(resp.text):
            link = _BING_LINK_PATTERN.search(block)
            if not link:
                continue
            snippet_match = _BING_SNIPPET_PATTERN.search(block)
            title = html.unescape(re.sub(r"<[^<]+?>", "", link.group("title")).strip())
            snippet = (
                html.unescape(re.sub(r"<[^<]+?>", "", snippet_match.group("snippet")).strip())
                if snippet_match
                else ""
            )
            if not title:
                continue
            results.append({"title": title, "snippet": snippet, "url": _resolve_bing_url(link.group("url")), "source": source})
            if len(results) >= limit:
                break
    return results


def search_web(
    query: str,
    limit: int = 10,
    timeout: float = 12.0,
) -> List[Dict[str, Any]]:
    """检索公开网页，依次尝试远端 MCP、外部网关、DuckDuckGo、Bing。

    Args:
        query: 搜索关键词或调研主题。
        limit: 返回的最大搜索条数。
        timeout: 单次抓取超时时间（秒）。

    Returns:
        结构化搜索结果列表，每项包含 ``title``、``snippet``、``url``；
        所有真实通道均无结果时返回空列表。
    """
    raise_if_cancelled()
    mcp_items = _normalize_items(
        search_web_via_mcp(query, limit=limit, timeout=max(timeout, 20.0)),
        limit,
    )
    if mcp_items:
        return mcp_items

    custom_url = os.environ.get("ECOM_WEB_SEARCH_URL", "").strip()
    if custom_url:
        try:
            with httpx.Client(timeout=timeout) as client:
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("ECOM_WEB_SEARCH_KEY", "").strip()
                if key:
                    headers["X-API-Key"] = key
                resp = client.post(custom_url, json={"query": query, "limit": limit}, headers=headers)
                resp.raise_for_status()
                gateway = result_items(
                    resp.json(),
                    "results",
                    limit,
                    required=("title", "url"),
                    url_required=True,
                )
                gateway = _normalize_items(gateway, limit, default_source="gateway")
                if gateway:
                    return gateway
        except Exception as exc:
            record_diagnostic("tool.gateway", "fallback", exc)

    raise_if_cancelled()
    try:
        duck_items = search_duckduckgo_html(query, limit=limit, timeout=timeout, source="duckduckgo")
    except Exception as exc:
        record_diagnostic("tool.search_duckduckgo", "fallback", exc)
        duck_items = []
    duck_items = _normalize_items(duck_items, limit)
    if duck_items:
        return duck_items

    raise_if_cancelled()
    try:
        bing_items = search_bing_html(query, limit=limit, timeout=timeout)
    except Exception as exc:
        record_diagnostic("tool.search_bing", "fallback", exc)
        bing_items = []
    bing_items = _normalize_items(bing_items, limit)
    if not bing_items:
        record_diagnostic("tool.search_empty", "fallback", None)
    return bing_items
