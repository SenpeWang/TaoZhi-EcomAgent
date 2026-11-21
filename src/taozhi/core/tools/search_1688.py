"""1688 源头工厂货源探查工具。

深入供应链批发渠道，提取一手出厂价格阶梯、起订量与供应商分布。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List

import httpx

from taozhi.config import resolve_runtime_dir

from taozhi.observability.tracing import record_diagnostic
from .outcome import ToolInvocationError, result_items


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
        商品列表，包含 ``title``, ``price``, ``sales``, ``shop``, ``url`` 等；合法查询未命中时返回空列表。
    """
    budget = max(1.0, float(timeout))
    custom_url = os.environ.get("ECOM_RESEARCH_P1688_URL", "").strip()
    if custom_url:
        try:
            with httpx.Client(timeout=min(20.0, budget)) as client:
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("ECOM_RESEARCH_P1688_KEY", "").strip()
                if key:
                    headers["X-API-Key"] = key
                resp = client.post(custom_url, json={"query": keyword, "limit": limit}, headers=headers)
                resp.raise_for_status()
                return result_items(resp.json(), "items", limit)
        except Exception as exc:
            record_diagnostic("tool.gateway", "fallback", exc)

    runtime_dir = resolve_runtime_dir()
    py = runtime_dir / "scraper-venv/bin/python"
    script = runtime_dir / "p1688_mcp/p1688_search.py"
    if not py.is_file() or not script.is_file():
        raise ToolInvocationError("tool.scraper_missing")
    proc = subprocess.run(
        [str(py), str(script), keyword, "--limit", str(limit)],
        capture_output=True, text=True, timeout=min(90.0, budget),
    )
    if proc.returncode:
        raise ToolInvocationError("tool.scraper_exit")
    for line in reversed((proc.stdout or "").splitlines()):
        if line.lstrip().startswith("{"):
            try:
                result = json.loads(line)
            except ValueError as exc:
                raise ToolInvocationError("tool.invalid_response") from exc
            if isinstance(result, dict) and result.get("state") == "ok":
                return result_items(result, "items", limit)
            break
    raise ToolInvocationError("tool.invalid_response")
