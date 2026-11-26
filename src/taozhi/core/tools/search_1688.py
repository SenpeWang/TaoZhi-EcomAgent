"""1688 源头工厂货源探查工具。

深入供应链批发渠道，提取一手出厂价格阶梯、起订量与供应商分布。
优先外部网关，其次本地 Playwright 采集；无匹配时返回空列表。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List

import httpx

from taozhi.config import resolve_runtime_dir
from taozhi.observability.tracing import record_diagnostic
from .outcome import ToolCancelled, ToolInvocationError, is_http_url, raise_if_cancelled


def _run_local_scraper(python_path: str, script_path: str, keyword: str, limit: int, budget: float) -> str:
    """启动本地采集脚本并轮询取消/超时，返回 stdout 文本。"""
    process = subprocess.Popen(
        [python_path, script_path, keyword, "--limit", str(limit)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + min(90.0, max(1.0, budget))
    try:
        while process.poll() is None:
            raise_if_cancelled()
            if time.monotonic() >= deadline:
                process.kill()
                raise TimeoutError("工具执行超时")
            time.sleep(0.1)
        stdout, _stderr = process.communicate()
    finally:
        if process.poll() is None:
            process.kill()
    if process.returncode:
        raise ToolInvocationError("tool.scraper_exit")
    return stdout or ""


def _text(value: Any) -> str | None:
    """把展示型字段归一为非空字符串或 None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    """只接受单一数值；区间、面议等无法解析的文本返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*[¥￥$]?\s*([0-9]+(?:\.[0-9]+)?)\s*元?\s*", value)
    return float(match.group(1)) if match else None


def _integer(value: Any) -> int | None:
    """只接受非负整数或含数字的文本（如“2 件起订”“1000+”）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    match = re.search(r"[0-9]+", value.replace(",", ""))
    return int(match.group(0)) if match else None


def _absolute_url(value: Any) -> str | None:
    """补齐协议相对或省略协议的链接，并拒绝非公网 HTTP(S) 地址。"""
    url = _text(value)
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif "://" not in url and re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/|$)", url):
        url = "https://" + url
    return url if is_http_url(url) else None


def _normalize_items(payload: object, limit: int, *, source: str) -> List[Dict[str, Any]]:
    """把网关或本地采集载荷归一为固定字段的货源条目。

    Args:
        payload: 采集通道返回的 JSON 载荷，需包含 ``items`` 数组。
        limit: 返回条数上限。
        source: 本条数据实际来自的通道名称。

    Returns:
        固定包含标题、链接、报价、起订量、店铺、销量与来源的条目列表；
        缺标题或缺可核对链接的条目不进入结果。
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ToolInvocationError("tool.invalid_response")
    cleaned: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        url = _absolute_url(item.get("url"))
        if not title or not url:
            continue
        price_text = _text(item.get("price_text") or item.get("price"))
        price_value = _number(item.get("price_value"))
        if price_value is None:
            price_value = _number(item.get("price"))
        sales_raw = item.get("sales")
        if sales_raw is None:
            sales_raw = item.get("sold")
        cleaned.append(
            {
                "title": title,
                "url": url,
                "price_text": price_text,
                "price_value": price_value,
                "currency": "CNY" if price_value is not None else None,
                "moq": _integer(item.get("moq")),
                "shop": _text(item.get("shop") or item.get("supplier")),
                "sales": _integer(sales_raw),
                "source": source,
            }
        )
        if len(cleaned) >= limit:
            break
    return cleaned


def search_1688_wholesale(
    keyword: str,
    limit: int = 20,
    timeout: float = 90.0,
) -> List[Dict[str, Any]]:
    """搜索 1688 批发平台真实货源（优先网关，其次本地 Playwright 抓手）。

    Args:
        keyword: 检索关键词。
        limit: 返回的最大商品条数。
        timeout: 超时预算（秒）。

    Returns:
        商品列表，每项固定包含 ``title``、``url``、``price_text``、``price_value``、
        ``currency``、``moq``、``shop``、``sales``、``source``；缺链接的条目被丢弃，
        合法查询未命中时返回空列表。
    """
    budget = max(1.0, float(timeout))
    custom_url = os.environ.get("ECOM_RESEARCH_P1688_URL", "").strip()
    if custom_url:
        try:
            with httpx.Client(timeout=min(90.0, budget)) as client:
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("ECOM_RESEARCH_P1688_KEY", "").strip()
                if key:
                    headers["X-API-Key"] = key
                resp = client.post(custom_url, json={"query": keyword, "limit": limit}, headers=headers)
                resp.raise_for_status()
                return _normalize_items(resp.json(), limit, source="1688-gateway")
        except ToolCancelled:
            raise
        except Exception as exc:
            record_diagnostic("tool.gateway", "fallback", exc)

    runtime_dir = resolve_runtime_dir()
    python_path = runtime_dir / "scraper-venv/bin/python"
    script_path = runtime_dir / "p1688_mcp/p1688_search.py"
    if not python_path.is_file() or not script_path.is_file():
        raise ToolInvocationError("tool.scraper_missing")

    raise_if_cancelled()
    output = _run_local_scraper(str(python_path), str(script_path), keyword, limit, budget)
    for line in reversed(output.splitlines()):
        if line.lstrip().startswith("{"):
            try:
                result = json.loads(line)
            except ValueError as exc:
                raise ToolInvocationError("tool.invalid_response") from exc
            if isinstance(result, dict) and result.get("state") == "ok":
                return _normalize_items(result, limit, source="1688-local")
            break
    raise ToolInvocationError("tool.invalid_response")
