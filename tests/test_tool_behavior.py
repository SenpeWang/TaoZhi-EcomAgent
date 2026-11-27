"""工具行为的降级、取消、来源标注、SSRF 固定与响应限制回归。"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock

import pytest

from taozhi.core.tools import search_amazon, search_1688, search_web, web_scraper
from taozhi.core.tools.outcome import ToolInvocationError, execute_tool_with_outcome, raise_if_cancelled, result_items


def test_search_web_falls_back_to_bing_when_duckduckgo_raises(monkeypatch):
    """DuckDuckGo 网络异常不能阻断后续 Bing 降级。"""
    monkeypatch.delenv("ECOM_WEB_SEARCH_URL", raising=False)
    monkeypatch.setattr(search_web, "search_web_via_mcp", lambda *_a, **_k: [])
    monkeypatch.setattr(search_web, "search_duckduckgo_html", Mock(side_effect=RuntimeError("timeout")))
    monkeypatch.setattr(
        search_web,
        "search_bing_html",
        lambda *_a, **_k: [{"title": "备用结果", "snippet": "摘要", "url": "https://example.com/a", "source": "bing"}],
    )
    items = search_web.search_web("手机壳", limit=3)
    assert [item["source"] for item in items] == ["bing"]


def test_search_web_drops_invalid_urls_and_dedupes(monkeypatch):
    """搜索结果只保留 http/https 链接，并按 URL 去重。"""
    monkeypatch.delenv("ECOM_WEB_SEARCH_URL", raising=False)
    monkeypatch.setattr(search_web, "search_web_via_mcp", lambda *_a, **_k: [])
    monkeypatch.setattr(
        search_web,
        "search_duckduckgo_html",
        lambda *_a, **_k: [
            {"title": "A", "snippet": "s1", "url": "https://example.com/a", "source": "duckduckgo"},
            {"title": "A 重复", "snippet": "s2", "url": "https://example.com/a", "source": "duckduckgo"},
            {"title": "非法", "snippet": "s3", "url": "javascript:alert(1)", "source": "duckduckgo"},
        ],
    )
    items = search_web.search_web("手机壳", limit=5)
    assert len(items) == 1 and items[0]["title"] == "A"


def test_gateway_missing_url_falls_back_to_duckduckgo(monkeypatch):
    """外部网关返回缺 URL 的条目时按格式异常降级，而不是进入证据池。"""
    monkeypatch.setenv("ECOM_WEB_SEARCH_URL", "https://gateway.test/search")
    monkeypatch.setattr(search_web, "search_web_via_mcp", lambda *_a, **_k: [])

    class _GatewayClient:
        """模拟业务网关客户端，返回固定检索结果。"""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def post(self, *_args, **_kwargs):
            """返回缺少链接的搜索结果响应桩。

            Args:
                _args: 被忽略的位置参数。
                _kwargs: 被忽略的关键字参数。
            """
            response = Mock()
            response.raise_for_status = Mock()
            response.json = Mock(return_value={"results": [{"title": "缺链接"}]})
            return response

    monkeypatch.setattr(search_web.httpx, "Client", lambda **_kw: _GatewayClient())
    monkeypatch.setattr(
        search_web,
        "search_duckduckgo_html",
        lambda *_a, **_k: [{"title": "真结果", "snippet": "s", "url": "https://example.com/real", "source": "duckduckgo"}],
    )
    items = search_web.search_web("手机壳", limit=5)
    assert items[0]["url"] == "https://example.com/real"


def test_amazon_fallback_marks_duckduckgo_source(monkeypatch):
    """Amazon 的 DuckDuckGo 回退结果必须标注 amazon-ddg，不能冒充直采。"""
    monkeypatch.setattr(
        search_amazon,
        "search_duckduckgo_html",
        lambda *_a, **_k: [
            {
                "title": "Case - Amazon.com",
                "snippet": "Protective cover $9.99",
                "url": "https://www.amazon.com/dp/B0REAL1234",
                "source": "duckduckgo",
            }
        ],
    )
    items = search_amazon._fallback_duckduckgo_amazon_search("case", limit=5)
    assert items and items[0]["source"] == "amazon-ddg"
    assert items[0]["asin"] == "B0REAL1234"


def test_cancellation_context_stops_running_tool():
    """运行中的工具在收到取消事件后通过协作式检查停止。"""
    event = threading.Event()

    def tool(**_kwargs):
        """可被取消的长耗时工具桩。

        Args:
            _kwargs: 被忽略的关键字参数。
        """
        for _ in range(200):
            raise_if_cancelled()
            time.sleep(0.01)
        return []

    threading.Timer(0.05, event.set).start()
    outcome = execute_tool_with_outcome("loop_tool", tool, {}, remaining_seconds=2, cancel_event=event)
    assert outcome.status == "cancelled"


def test_result_items_rejects_incomplete_items():
    """采集结果缺必填字段时必须报格式异常。"""
    with pytest.raises(ToolInvocationError):
        result_items({"items": [{"title": ""}]}, "items", 5)
    with pytest.raises(ToolInvocationError):
        result_items({"results": [{"title": "t", "url": "javascript:x"}]}, "results", 5, url_required=True)


class _Stream:
    """构造可迭代字节流响应桩。"""

    encoding = "utf-8"

    def __init__(self, *, status_code, headers, chunks=()):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_bytes(self):
        """逐块产出预置字节。"""
        yield from self._chunks


class _Client:
    """返回预设流响应的 httpx 客户端桩。"""

    def __init__(self, stream):
        self._stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stream(self, *_args, **_kwargs):
        """返回预设的流响应。

        Args:
            _args: 被忽略的位置参数。
            _kwargs: 被忽略的关键字参数。
        """
        return self._stream


def _scrape_with(monkeypatch, stream, *, mcp=None, **kwargs):
    """在替换网络与 MCP 依赖后执行网页抓取。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        stream: 响应流桩对象。
        kwargs: 关键字参数。
        mcp: MCP 抓取替身函数。
    """
    monkeypatch.setattr(web_scraper, "resolve_public_http_target", lambda url: (url, "93.184.216.34"))
    monkeypatch.setattr(web_scraper.httpx, "Client", lambda **_kw: _Client(stream))
    monkeypatch.setattr(web_scraper, "fetch_page_via_mcp", mcp or Mock(return_value=""))
    return web_scraper.fetch_web_page("https://example.com/article", **kwargs)


def test_web_scraper_rejects_oversize_response(monkeypatch):
    """Content-Length 超过上限时直接拒绝，不读取正文。"""
    result = _scrape_with(
        monkeypatch,
        _Stream(status_code=200, headers={"content-type": "text/html", "content-length": str(3 * 1024 * 1024)}),
    )
    assert result["status"] == "error" and result["error_code"] == "RESPONSE_TOO_LARGE"
    assert result["text"] == "" and result["truncated"] is False


def test_web_scraper_rejects_non_text_content(monkeypatch):
    """图片等非文本响应不进入正文提取。"""
    result = _scrape_with(monkeypatch, _Stream(status_code=200, headers={"content-type": "image/png"}))
    assert result["status"] == "error" and result["error_code"] == "CONTENT_TYPE"


def test_pinned_transport_fetches_through_validated_ip(monkeypatch):
    """域名连接固定到已校验 IP，仍能完成正常抓取。"""

    class Handler(BaseHTTPRequestHandler):
        """本地 HTTP 服务器处理器，返回最小 HTML 页面。"""

        def do_GET(self):
            """返回最小 HTML 页面。"""
            body = b"<html><head><title>hello</title></head><body><p>world</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """静默服务器访问日志。

            Args:
                _args: 被忽略的位置参数。
            """
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    monkeypatch.setattr(
        web_scraper,
        "resolve_public_http_target",
        lambda _url: (f"http://example.test:{port}/", "127.0.0.1"),
    )
    try:
        result = web_scraper.fetch_web_page(f"http://example.test:{port}/")
    finally:
        server.shutdown()
    assert result["status"] == "ok" and result["title"] == "hello" and "world" in result["text"]


def test_1688_local_scraper_uses_utf8_and_returns_stdout(monkeypatch):
    """本地采集子进程显式使用 UTF-8，避免中文货源标题乱码。"""
    captured: dict = {}

    class FakeProcess:
        """立即成功的子进程桩，记录启动参数。"""

        returncode = 0

        def __init__(self, args, **kwargs):
            captured.update(kwargs)
            captured["args"] = args

        def poll(self):
            """返回成功退出码。"""
            return 0

        def communicate(self):
            """返回预置的 JSON 输出。"""
            return ('{"state":"ok","items":[{"title":"东莞源头厂"}]}\n', "")

    monkeypatch.setattr(search_1688.subprocess, "Popen", FakeProcess)
    output = search_1688._run_local_scraper("python", "script.py", "保温杯", 2, 5)
    assert "东莞源头厂" in output
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"

def test_1688_local_scraper_kills_process_on_cancel(monkeypatch):
    """取消信号到达时，本地采集子进程必须被 kill，不能继续运行。"""
    from taozhi.core.tools.outcome import _CANCEL_EVENT, ToolCancelled

    killed = {"count": 0}

    class FakeProcess:
        """可被终止的挂起子进程桩。"""

        returncode = None

        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            """保持运行中状态。"""
            return None

        def kill(self):
            """记录终止次数。"""
            killed["count"] += 1

        def communicate(self):
            """返回空输出。"""
            return "", ""

    monkeypatch.setattr(search_1688.subprocess, "Popen", FakeProcess)
    event = threading.Event()
    event.set()
    token = _CANCEL_EVENT.set(event)
    try:
        with pytest.raises(ToolCancelled):
            search_1688._run_local_scraper("python", "script.py", "保温杯", 2, 5)
    finally:
        _CANCEL_EVENT.reset(token)
    assert killed["count"] >= 1


def test_1688_normalization_requires_verifiable_url_and_keeps_ranges_null():
    """1688 条目缺可核对链接时丢弃，报价区间不解析成假数值。"""
    items = search_1688._normalize_items(
        {
            "items": [
                {
                    "title": "有效货源",
                    "url": "detail.1688.com/offer/1.html",
                    "price": "18.5-25",
                    "moq": "2 件起订",
                    "shop": "东莞厂",
                },
                {"title": "缺链接", "price": 10},
                {"title": "非法链接", "url": "javascript:alert(1)"},
            ]
        },
        10,
        source="1688-gateway",
    )
    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://detail.1688.com/offer/1.html"
    assert item["price_text"] == "18.5-25"
    assert item["price_value"] is None
    assert item["currency"] is None
    assert item["moq"] == 2
    assert item["source"] == "1688-gateway"


def test_amazon_fallback_marks_search_snippet_price_and_unknown_fields(monkeypatch):
    """回退价格必须标注来自搜索摘要，未知评分与 Prime 一律为 null。"""
    monkeypatch.setattr(
        search_amazon,
        "search_duckduckgo_html",
        lambda *_args, **_kwargs: [
            {
                "title": "Case - Amazon.com",
                "snippet": "Protective cover $9.99",
                "url": "https://www.amazon.com/dp/B0REAL1234",
                "source": "duckduckgo",
            }
        ],
    )
    item = search_amazon._fallback_duckduckgo_amazon_search("case", limit=5)[0]
    assert item["price_source"] == "search_snippet"
    assert item["price_text"] == "$9.99"
    assert item["price_value"] == 9.99
    assert item["currency"] == "USD"
    assert item["rating_value"] is None
    assert item["is_prime"] is None


def test_web_scraper_marks_truncated_body(monkeypatch):
    """正文被 max_chars 截断时必须显式标记，避免模型误判内容完整。"""
    body = b"<html><head><title>t</title></head><body>" + b"x" * 500 + b"</body></html>"
    result = _scrape_with(
        monkeypatch,
        _Stream(status_code=200, headers={"content-type": "text/html"}, chunks=(body,)),
        max_chars=200,
    )
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert len(result["text"]) == 200
    assert result["status_code"] == 200
    assert result["error"] is None
    assert result["error_code"] is None
