"""全网实时搜索与关键词精准抽取工具。

集成自建电商检索网关与 DuckDuckGo 纯 HTML 双链路容灾抓取。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List
from urllib.parse import unquote

import httpx

def extract_search_keyword(text: str) -> str:
    """从任务指令或长文本中精准提取核心检索关键词。

    例如：
    - "分析电商零售端关于【智能保温杯】的最新热门产品" -> "智能保温杯"
    - "关于'无糖即饮茶'的市场表现调研" -> "无糖即饮茶"
    - "扫地机器人" -> "扫地机器人"

    Args:
        text (str): 输入的原始长指令或查询句。

    Returns:
        str: 提炼出的高准确度搜索词。
    """
    if not text:
        return ""
    # 优先抽取中文括号、双引号、单引号包围的实体词
    m = re.search(r"[【“\"「『](.+?)[】”\"」』]", text)
    if m:
        candidate = m.group(1).strip()
        if candidate:
            return candidate

    clean = text.strip().split("\n")[0]
    # 若本身较短则直接使用
    if len(clean) <= 20:
        return clean

    # 移除常见的任务指令引导前缀
    clean = re.sub(
        r"^(请|分析|调研|检索|搜索|查找|采集|获取|关于|针对|深入挖掘|排查|梳理|对比|汇总)+",
        "",
        clean,
    ).strip()
    # 截断过长字符，避免搜索引擎拒绝长串
    return clean[:25].strip()



def search_web(query: str, limit: int = 10, timeout: float = 12.0) -> List[Dict[str, Any]]:
    """执行全网实时搜索（内置 DuckDuckGo HTML 解析与自建网关适配）。

    优先使用环境变量 ECOM_WEB_SEARCH_URL 配置的自建检索服务，
    未配置时自动回退到稳定且零 API-Key 的 DuckDuckGo 纯文本解析链路。

    Args:
        query (str): 搜索关键词或调研主题。
        limit (int): 返回的最大搜索条数，默认 10。
        timeout (float): 抓取超时时间（秒）。

    Returns:
        List[Dict[str, Any]]: 结构化搜索结果列表，每项包含 title, snippet, url。
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
        except Exception:
            pass  # 降级到内置链路

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
            if resp.status_code == 200:
                raw_html = resp.text
                # 正则提取结果卡片
                pattern = re.compile(
                    r'<a class="result__url"[^>]*href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>.*?'
                    r'<a[^>]*>(?P<title>.*?)</a>.*?<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                    re.DOTALL,
                )
                for m in pattern.finditer(raw_html):
                    clean_title = re.sub(r"<[^<]+?>", "", m.group("title")).strip()
                    clean_title = html.unescape(clean_title)
                    clean_snippet = re.sub(r"<[^<]+?>", "", m.group("snippet")).strip()
                    clean_snippet = html.unescape(clean_snippet)
                    raw_url = m.group("url")

                    # 解析 DuckDuckGo 的实际重定向目标链接
                    actual_url = raw_url
                    if "uddg=" in raw_url:
                        u_match = re.search(r"uddg=([^&]+)", raw_url)
                        if u_match:
                            actual_url = unquote(u_match.group(1))

                    if clean_title and clean_snippet:
                        results.append({
                            "title": clean_title,
                            "snippet": clean_snippet,
                            "url": actual_url,
                            "source": "duckduckgo",
                        })
                    if len(results) >= limit:
                        break
    except Exception:
        pass

    return results
