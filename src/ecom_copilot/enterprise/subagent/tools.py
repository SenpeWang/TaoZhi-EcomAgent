"""多智能体专属工具库（Web Search & E-Commerce Crawling Tools）。

自研工具集架构设计：
1. 粒度明确的原子工具；
2. 异常自愈与多路降级（支持 DuckDuckGo 纯 HTML 抓取、1688 批发平台、自建抓取网关）；
3. 严格遵循 Google Python Style Guide 注释。
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
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


def search_1688_wholesale(keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
    """搜索 1688 批发平台真实货源（优先 CPS 推广页免登录抓取）。

    Args:
        keyword (str): 检索关键词。
        limit (int): 返回的最大商品条数。

    Returns:
        List[Dict[str, Any]]: 商品列表，包含 title, price, sales, shop, url 等。
    """
    # 优先检测自建 1688 端点
    custom_url = os.environ.get("ECOM_RESEARCH_P1688_URL", "").strip()
    if custom_url:
        try:
            with httpx.Client(timeout=20.0) as client:
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("ECOM_RESEARCH_P1688_KEY", "").strip()
                if key:
                    headers["X-API-Key"] = key
                resp = client.post(custom_url, json={"query": keyword, "limit": limit}, headers=headers)
                resp.raise_for_status()
                return resp.json().get("items", [])[:limit]
        except Exception:
            pass

    # 本地 Playwright 抓取器
    py = Path.home() / ".local/share/ecom-agent/scraper-venv/bin/python"
    script = Path.home() / ".local/share/ecom-agent/p1688_mcp/p1688_search.py"
    if py.exists() and script.exists():
        try:
            proc = subprocess.run(
                [str(py), str(script), keyword, "--limit", str(limit)],
                capture_output=True, text=True, timeout=90,
            )
            for line in reversed((proc.stdout or "").strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    res = json.loads(line)
                    if res.get("state") == "ok":
                        return res.get("items", [])[:limit]
        except Exception:
            pass

    return []
