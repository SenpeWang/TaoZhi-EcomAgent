"""网页深度抓取与正文清洗提取器（内置逐跳 SSRF 边界校验）。

过滤 HTML script、style、SVG 等标签噪音，提取纯净正文供智能体研判；
所有出网请求与重定向跳转目标必须通过领域级 URL 公网合法性检查。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict
from urllib.parse import urljoin

import httpx

from taozhi.domain.errors import Denied
from taozhi.domain.url_guard import validate_public_http_url

_MAX_REDIRECTS = 3


def fetch_web_page(url: str, max_chars: int = 4000, timeout: float = 10.0) -> Dict[str, Any]:
    """抓取公网目标网页并清洗提取核心正文文本（禁止 SSRF 访问私网）。

    去除 script、style、导航栏及 HTML 标签噪音，提取纯文本供模型深入研判。

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
        )
    }
    try:
        current_url = validate_public_http_url(url)
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=False) as client:
            resp: httpx.Response | None = None
            for _ in range(_MAX_REDIRECTS + 1):
                resp = client.get(current_url)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if not location:
                        return {"url": current_url, "status": "error", "text": "重定向缺少目标地址", "title": ""}
                    current_url = validate_public_http_url(urljoin(current_url, location))
                    continue
                break
            else:
                return {"url": current_url, "status": "error", "text": "重定向次数超过安全上限", "title": ""}

            if resp is None or resp.status_code != 200:
                code = resp.status_code if resp is not None else "unknown"
                return {"url": current_url, "status": "error", "text": f"HTTP {code}", "title": ""}

            content = resp.text
            title_m = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"<[^<]+?>", "", title_m.group(1)).strip() if title_m else ""

            cleaned = re.sub(
                r"<(script|style|svg|noscript)[^>]*>.*?</\1>",
                "",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            text = re.sub(r"<[^<]+?>", " ", cleaned)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()

            return {
                "url": current_url,
                "status": "ok",
                "title": title[:150],
                "text": text[:max_chars],
            }
    except Denied as exc:
        return {"url": url, "status": "error", "error": exc.code, "text": exc.message, "title": ""}
    except Exception as exc:
        return {"url": url, "status": "error", "text": f"抓取异常: {type(exc).__name__}", "title": ""}
