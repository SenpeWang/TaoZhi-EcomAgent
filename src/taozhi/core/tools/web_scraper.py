"""网页深度抓取与正文清洗提取器（内置逐跳 SSRF 校验与 DNS 固定）。

过滤 HTML script、style、SVG 等标签噪音，提取纯净正文供智能体研判；
所有出网请求与重定向跳转目标必须通过领域级 URL 公网合法性检查，
TCP 连接固定到已校验 IP，避免 DNS 重绑定；仅接受文本响应并限制响应大小。
"""
from __future__ import annotations

import html
import os
import re
from typing import Any, Dict
from urllib.parse import urljoin, urlsplit

import httpx

from taozhi.core.tools.egress import PinnedTransport
from taozhi.core.tools.outcome import ToolCancelled, raise_if_cancelled
from taozhi.domain.errors import Denied
from taozhi.domain.url_guard import resolve_public_http_target
from taozhi.infrastructure.mcp.remote_search import fetch_page_via_mcp
from taozhi.observability.tracing import record_diagnostic

_MAX_REDIRECTS = 3
_MAX_BYTES = 2 * 1024 * 1024
_TEXT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "application/xhtml+xml",
        "application/json",
        "application/xml",
        "text/xml",
    }
)


def _error(
    url: str,
    message: str,
    code: str = "",
    status_code: int | None = None,
) -> Dict[str, Any]:
    """构造统一的抓取错误回执；错误原因不占用正文文本。

    Args:
        url: 目标网页地址。
        message: 可展示的中文失败原因。
        code: 稳定的故障码，用于诊断与统计。
        status_code: 已读取到的 HTTP 状态码，未发送请求时为 None。

    Returns:
        字段固定的错误回执，``text`` 恒为空串。
    """
    return {
        "url": url,
        "status": "error",
        "title": "",
        "text": "",
        "truncated": False,
        "status_code": status_code,
        "error": message,
        "error_code": code or None,
    }


def _content_type_allowed(value: str) -> bool:
    """仅放行文本类响应，缺失 Content-Type 时按文本处理。"""
    media_type = value.split(";", 1)[0].strip().lower()
    return not media_type or media_type in _TEXT_CONTENT_TYPES


def _fetch_with_proxy(url: str, headers: dict[str, str], char_limit: int, timeout: float) -> Dict[str, Any] | None:
    """直连受限时通过受控 HTTP 代理抓取公开网页；仍逐跳校验公网目标。"""
    if not (os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")):
        return None
    current_url = url
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            current_url, _ = resolve_public_http_target(current_url)
            with httpx.Client(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
                trust_env=True,
            ) as client:
                response = client.get(current_url)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue
            if response.status_code != 200 or not _content_type_allowed(response.headers.get("content-type", "")):
                return None
            if len(response.content) > _MAX_BYTES:
                return None
            content = response.content.decode(response.encoding or "utf-8", errors="replace")
            title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"<[^<]+?>", "", title_match.group(1)).strip() if title_match else ""
            cleaned = re.sub(
                r"<(script|style|svg|noscript)[^>]*>.*?</\1>",
                "",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            text = html.unescape(re.sub(r"<[^<]+?>", " ", cleaned))
            body = re.sub(r"\s+", " ", text).strip()
            return {
                "url": current_url,
                "status": "ok",
                "title": title[:150],
                "text": body[:char_limit],
                "truncated": len(body) > char_limit,
                "status_code": response.status_code,
                "error": None,
                "error_code": None,
            }
    except Exception as exc:
        record_diagnostic("tool.proxy", "failed", exc)
    return None


def fetch_web_page(url: str, max_chars: int = 4000, timeout: float = 10.0) -> Dict[str, Any]:
    """抓取公网目标网页并清洗提取核心正文文本（禁止 SSRF 访问私网）。

    Args:
        url: 目标网页 URL（仅允许公网 HTTP/HTTPS）。
        max_chars: 最多截取的纯文字数。
        timeout: 单跳请求超时时间（秒）。

    Returns:
        包含 ``url``、``status``、``text``、``title`` 的字典；被安全拦截或失败时 ``status='error'``。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    }
    char_limit = max(200, min(int(max_chars), 20000))
    current_url = str(url or "").strip()
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            raise_if_cancelled()
            current_url, target_ip = resolve_public_http_target(current_url)
            host = urlsplit(current_url).hostname or ""
            transport = PinnedTransport(host, target_ip)
            try:
                with httpx.Client(
                    transport=transport,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    with client.stream("GET", current_url) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location", "")
                            if not location:
                                record_diagnostic("tool.redirect", "failed")
                                return _error(
                                    current_url,
                                    "重定向缺少目标地址",
                                    status_code=response.status_code,
                                )
                            current_url = urljoin(current_url, location)
                            continue

                        if response.status_code != 200:
                            record_diagnostic("tool.http", "failed", http_status=response.status_code)
                            proxy_result = _fetch_with_proxy(current_url, headers, char_limit, timeout)
                            if proxy_result:
                                return proxy_result
                            fallback_text = fetch_page_via_mcp(current_url, timeout=max(timeout, 20.0))
                            if fallback_text:
                                return {
                                    "url": current_url,
                                    "status": "ok",
                                    "title": "",
                                    "text": fallback_text[:char_limit],
                                    "truncated": len(fallback_text) > char_limit,
                                    "status_code": response.status_code,
                                    "error": None,
                                    "error_code": None,
                                }
                            return _error(
                                current_url,
                                f"HTTP {response.status_code}",
                                status_code=response.status_code,
                            )

                        if not _content_type_allowed(response.headers.get("content-type", "")):
                            record_diagnostic("tool.content_type", "failed")
                            return _error(
                                current_url,
                                "目标返回非文本内容",
                                "CONTENT_TYPE",
                                status_code=response.status_code,
                            )

                        raw_length = response.headers.get("content-length", "")
                        if raw_length.isdigit() and int(raw_length) > _MAX_BYTES:
                            record_diagnostic("tool.response_too_large", "failed")
                            return _error(
                                current_url,
                                "响应体超过大小上限",
                                "RESPONSE_TOO_LARGE",
                                status_code=response.status_code,
                            )

                        chunks = []
                        total = 0
                        for chunk in response.iter_bytes():
                            raise_if_cancelled()
                            total += len(chunk)
                            if total > _MAX_BYTES:
                                record_diagnostic("tool.response_too_large", "failed")
                                return _error(
                                    current_url,
                                    "响应体超过大小上限",
                                    "RESPONSE_TOO_LARGE",
                                    status_code=response.status_code,
                                )
                            chunks.append(chunk)

                        encoding = response.encoding or "utf-8"
                        content = b"".join(chunks).decode(encoding, errors="replace")
                        title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
                        title = re.sub(r"<[^<]+?>", "", title_match.group(1)).strip() if title_match else ""

                        cleaned = re.sub(
                            r"<(script|style|svg|noscript)[^>]*>.*?</\1>",
                            "",
                            content,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        text = re.sub(r"<[^<]+?>", " ", cleaned)
                        text = html.unescape(text)
                        text = re.sub(r"\s+", " ", text).strip()

                        truncated = len(text) > char_limit
                        return {
                            "url": current_url,
                            "status": "ok",
                            "title": title[:150],
                            "text": text[:char_limit],
                            "truncated": truncated,
                            "status_code": response.status_code,
                            "error": None,
                            "error_code": None,
                        }
            finally:
                transport.close()

        record_diagnostic("tool.redirect", "failed")
        return _error(current_url, "重定向次数超过安全上限")
    except ToolCancelled:
        raise
    except Denied as exc:
        record_diagnostic("permission.denied" if exc.status == 403 else "tool.error", "failed", exc)
        return _error(url, exc.message, exc.code)
    except Exception as exc:
        record_diagnostic("tool.timeout" if isinstance(exc, httpx.TimeoutException) else "tool.error", "failed", exc)
        proxy_result = _fetch_with_proxy(url, headers, char_limit, timeout)
        if proxy_result:
            return proxy_result
        fallback_text = fetch_page_via_mcp(url, timeout=max(timeout, 20.0))
        if fallback_text:
            return {
                "url": url,
                "status": "ok",
                "title": "",
                "text": fallback_text[:char_limit],
                "truncated": len(fallback_text) > char_limit,
                "status_code": None,
                "error": None,
                "error_code": None,
            }
        return _error(url, f"抓取异常: {type(exc).__name__}")
