"""网页深度抓取与正文清洗提取器。

过滤 HTML script、style、SVG 等标签噪音，提取纯净正文供智能体研判。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict

import httpx

def fetch_web_page(url: str, max_chars: int = 4000, timeout: float = 10.0) -> Dict[str, Any]:
    """抓取目标网页并清洗提取核心正文文本。

    去除 script、style、导航栏及 HTML 标签噪音，提取纯文本供模型深入研判。

    Args:
        url (str): 目标网页 URL。
        max_chars (int): 最多截取的纯文字数。
        timeout (float): 请求超时时间。

    Returns:
        Dict[str, Any]: 包含 url, status, text, title 的字典。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return {"url": url, "status": "error", "text": f"HTTP {resp.status_code}", "title": ""}

            content = resp.text
            # 提取 title
            title_m = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"<[^<]+?>", "", title_m.group(1)).strip() if title_m else ""

            # 过滤 script, style, svg
            cleaned = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", "", content, flags=re.IGNORECASE | re.DOTALL)
            # 过滤全部 HTML 标签
            text = re.sub(r"<[^<]+?>", " ", cleaned)
            text = html.unescape(text)
            # 压缩空白
            text = re.sub(r"\s+", " ", text).strip()

            return {
                "url": url,
                "status": "ok",
                "title": title,
                "text": text[:max_chars],
            }
    except Exception as exc:
        return {"url": url, "status": "error", "text": str(exc), "title": ""}
