"""全网网页实时搜索工具（基于 DuckDuckGo 无密免登录链路 + 自建检索网关扩展）。

提取标题、核心摘要与真实网页链接供多智能体开展竞品舆情、市场趋势与差评洞察分析。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, Dict, List
from urllib.parse import unquote

import httpx

_LOG = logging.getLogger(__name__)

_DDG_RESULT_PATTERN = re.compile(
    r'<a class="result__url"[^>]*href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>.*?'
    r'<a[^>]*>(?P<title>.*?)</a>.*?<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)


def extract_search_keyword(user_query: str) -> str:
    """从自然语言指令中提炼最精准的核心商品品类或搜索短语。

    Args:
        user_query: 用户原始的电商调研提问或检索诉求。

    Returns:
        提炼后的紧凑核心关键词。
    """
    q = re.sub(r"[，。！？、；：\"'“”‘’()（）【】\[\]]+", " ", user_query)
    stop_phrases = [
        "帮我", "请帮我", "调研一下", "调研", "分析一下", "分析", "市场趋势", "和", "与",
        "竞争格局", "消费者痛点", "差评原因", "卖点", "怎么样", "如何", "我想了解",
        "目前", "2026年", "2025年", "最新的", "行情", "情况", "数据", "搜一下",
        "查一下", "查找", "检索", "市场", "电商", "行业", "怎么看", "值不值得",
        "多少钱", "价格是多少", "是什么", "有哪些", "在", "上", "的", "怎么",
        "适合", "能用吗", "可以用", "是不是", "推荐", "对比", "为什么",
    ]
    for sp in stop_phrases:
        q = q.replace(sp, " ")
    cleaned = re.sub(r"\s+", " ", q).strip()
    return cleaned if len(cleaned) >= 2 else user_query.strip()[:30]


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
        结构化检索结果列表，抓取失败或无结果时返回空列表 ``[]``。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    results: List[Dict[str, Any]] = []
    try:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            resp = client.post("https://html.duckduckgo.com/html/", data={"q": query})
            if resp.status_code != 200:
                return results
            for m in _DDG_RESULT_PATTERN.finditer(resp.text):
                clean_title = html.unescape(re.sub(r"<[^<]+?>", "", m.group("title")).strip())
                clean_snippet = html.unescape(re.sub(r"<[^<]+?>", "", m.group("snippet")).strip())
                raw_url = m.group("url")
                actual_url = raw_url
                if "uddg=" in raw_url:
                    u_match = re.search(r"uddg=([^&]+)", raw_url)
                    if u_match:
                        actual_url = unquote(u_match.group(1))
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
    except Exception as exc:
        _LOG.warning("DuckDuckGo 全网检索失败 (%s): %s", query[:40], exc)
    return results


def search_web(
    query: str,
    limit: int = 10,
    timeout: float = 12.0,
) -> List[Dict[str, Any]]:
    """全网电商市场调研与消费评价实时搜索引擎。

    优先请求自建外部搜索网关（通过环境变量 ``ECOM_WEB_SEARCH_URL`` 配置）；
    未配置时回退至 ``search_duckduckgo_html`` 无密链路。

    Args:
        query: 搜索关键词或调研主题。
        limit: 返回的最大搜索条数，默认 10。
        timeout: 抓取超时时间（秒）。

    Returns:
        结构化搜索结果列表，每项包含 ``title``, ``snippet``, ``url``。
    """
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
                return resp.json().get("results", [])[:limit]
        except Exception as exc:
            _LOG.warning("自定义全网检索网关不可用，降级为 DuckDuckGo: %s", exc)

    return search_duckduckgo_html(query, limit=limit, timeout=timeout, source="duckduckgo")
