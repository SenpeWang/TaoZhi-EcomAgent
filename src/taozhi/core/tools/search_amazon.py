"""Amazon 全球站跨境商品搜索与结构化提取工具（零虚假兜底）。

支持多级降级链路：
1. 若配置了外部亚马逊网关 (``ECOM_AMAZON_SEARCH_URL``)，优先走外部稳定 API；
2. 否则直接通过无密 HTTP 抓包解析 Amazon.com 搜索页 DOM；
3. 若被反爬拦截，降级复用 DuckDuckGo ``site:amazon.com`` 定向抓取，来源标记为 ``amazon-ddg``；
4. 若所有真实通道均无结果，诚实返回空列表 ``[]``（严禁捏造测试 ASIN 或假价格）。
"""
from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List
from urllib.parse import quote_plus

import httpx

from .search_web import search_duckduckgo_html
from taozhi.observability.tracing import record_diagnostic
from .outcome import dedupe_items, raise_if_cancelled, result_items

AMAZON_BASE_URL = "https://www.amazon.com/"


def search_amazon(
    query: str,
    limit: int = 10,
    timeout: float = 12.0,
) -> List[Dict[str, Any]]:
    """检索 Amazon 平台跨境真实商品定价、ASIN 编码与市场表现。

    Args:
        query: 检索关键词（英文或中文，将自动适配搜索）。
        limit: 返回商品条目上限，默认 10 条。
        timeout: HTTP 请求超时时间（秒），默认 12 秒。

    Returns:
        结构化亚马逊商品列表；未抓到真实商品时返回空列表 ``[]``。
    """
    clean_query = query.strip()
    if not clean_query:
        return []
    raise_if_cancelled()

    custom_url = os.environ.get("ECOM_AMAZON_SEARCH_URL", "").strip()
    if custom_url:
        try:
            with httpx.Client(timeout=timeout) as client:
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("ECOM_AMAZON_SEARCH_KEY", "").strip()
                if key:
                    headers["X-API-Key"] = key
                resp = client.post(custom_url, json={"query": clean_query, "limit": limit}, headers=headers)
                resp.raise_for_status()
                items = result_items(resp.json(), "items", limit, required=("title",))
                if items:
                    return dedupe_items(items)[:limit]
        except Exception as exc:
            record_diagnostic("tool.gateway", "fallback", exc)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.amazon.com/",
    }

    results: List[Dict[str, Any]] = []
    search_url = f"{AMAZON_BASE_URL}s?k={quote_plus(clean_query)}"

    try:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(search_url)
            if resp.status_code == 200 and "api-services-support@amazon.com" not in resp.text:
                raw_html = resp.text
                cards = re.findall(
                    r'<div[^>]+data-component-type="s-search-result"[^>]*data-asin="([A-Z0-9]{10})"[^>]*>(.*?)(?=<div[^>]+data-component-type="s-search-result"|$)',
                    raw_html,
                    re.DOTALL,
                )
                for asin, card_html in cards:
                    raise_if_cancelled()
                    try:
                        title_m = re.search(r'<span class="a-size-[^"]*a-text-normal"[^>]*>(.*?)</span>', card_html, re.DOTALL)
                        if not title_m:
                            title_m = re.search(r'<h2[^>]*>.*?<span[^>]*>(.*?)</span>', card_html, re.DOTALL)
                        if not title_m:
                            continue
                        title = html.unescape(re.sub(r"<[^<]+?>", "", title_m.group(1)).strip())

                        price_m = re.search(r'<span class="a-offscreen"[^>]*>(\$[0-9,.]+)</span>', card_html)
                        if not price_m:
                            price_m = re.search(r'<span class="a-price-whole">([0-9,]+)</span>.*?<span class="a-price-fraction">([0-9]+)</span>', card_html, re.DOTALL)
                            price_str = f"${price_m.group(1)}.{price_m.group(2)}" if price_m else ""
                        else:
                            price_str = price_m.group(1)

                        rating_m = re.search(r'<span class="a-icon-alt"[^>]*>([0-9.]+ out of 5 stars)</span>', card_html)
                        rating = rating_m.group(1) if rating_m else ""

                        reviews_m = re.search(r'<span class="a-size-base s-underline-text"[^>]*>([0-9,]+)</span>', card_html)
                        reviews_cnt = int(reviews_m.group(1).replace(",", "")) if reviews_m else None

                        price_val = None
                        if price_str:
                            try:
                                price_val = float(price_str.replace("$", "").replace(",", ""))
                            except ValueError:
                                price_val = None

                        results.append(
                            {
                                "title": title,
                                "url": f"{AMAZON_BASE_URL}dp/{asin}",
                                "asin": asin,
                                "price_text": price_str or None,
                                "price_value": price_val,
                                "currency": "USD" if price_val is not None else None,
                                "rating_value": float(rating.split(" ")[0]) if rating else None,
                                "reviews_count": reviews_cnt,
                                "is_prime": True if "a-icon-prime" in card_html else None,
                                "price_source": "product_page",
                                "source": "amazon",
                            }
                        )
                    except (ValueError, AttributeError):
                        continue
                    if len(results) >= limit:
                        return dedupe_items(results)[:limit]
            else:
                record_diagnostic("tool.scraper", "fallback", http_status=resp.status_code)
    except Exception as exc:
        record_diagnostic("tool.scraper", "fallback", exc)

    if not results:
        try:
            results = _fallback_duckduckgo_amazon_search(clean_query, limit=limit, timeout=timeout)
        except Exception as exc:
            record_diagnostic("tool.scraper", "fallback", exc)
            results = []

    return dedupe_items(results)[:limit]


def _fallback_duckduckgo_amazon_search(
    query: str, limit: int = 10, timeout: float = 12.0
) -> List[Dict[str, Any]]:
    """通过 DuckDuckGo ``site:amazon.com`` 提取真实 Amazon 页面，来源标记为二手检索。"""
    raise_if_cancelled()
    ddg_items = search_duckduckgo_html(
        f"site:amazon.com {query}",
        limit=max(limit * 2, limit),
        timeout=timeout,
        source="amazon-ddg",
    )
    fallback_items: List[Dict[str, Any]] = []
    for row in ddg_items:
        raise_if_cancelled()
        actual_url = row.get("url", "")
        if "amazon.com" not in actual_url:
            continue
        clean_title = re.sub(r":\s*Amazon\.com:.*$", "", row.get("title", "")).strip()
        clean_title = re.sub(r"\s*-\s*Amazon\.com.*$", "", clean_title).strip()
        clean_snippet = row.get("snippet", "")

        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", actual_url)
        asin = asin_match.group(1) if asin_match else ""

        price_match = re.search(r"\$([0-9]+(?:\.[0-9]{2})?)", clean_snippet)
        price_str = f"${price_match.group(1)}" if price_match else ""
        price_val = float(price_match.group(1)) if price_match else None

        if not clean_title:
            continue
        fallback_items.append(
            {
                "title": clean_title,
                "url": actual_url,
                "asin": asin or None,
                "price_text": price_str or None,
                "price_value": price_val,
                "currency": "USD" if price_val is not None else None,
                "rating_value": None,
                "reviews_count": None,
                "is_prime": None,
                "price_source": "search_snippet",
                "source": "amazon-ddg",
            }
        )
    return dedupe_items(fallback_items)[:limit]
