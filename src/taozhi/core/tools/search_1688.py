"""1688 源头工厂货源探查工具。

深入供应链批发渠道，提取一手出厂价格阶梯、起订量与供应商分布。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import httpx

def search_1688_wholesale(
    keyword: str,
    limit: int = 20,
    timeout: float = 90.0,
) -> List[Dict[str, Any]]:
    """搜索 1688 批发平台真实货源（优先 CPS 推广页免登录抓取）。

    Args:
        keyword (str): 检索关键词。
        limit (int): 返回的最大商品条数。
        timeout (float): 超时预算（秒）。

    Returns:
        List[Dict[str, Any]]: 商品列表，包含 title, price, sales, shop, url 等。
    """
    budget = max(1.0, float(timeout))
    # 优先检测自建 1688 端点
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
                capture_output=True, text=True, timeout=min(90.0, budget),
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
