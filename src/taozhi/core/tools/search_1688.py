"""1688 源头工厂货源探查工具。

深入供应链批发渠道，提取一手出厂价格阶梯、起订量与供应商分布。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import httpx

_LOG = logging.getLogger(__name__)


def _resolve_runtime_dir() -> Path:
    """按环境变量或默认位置解析运维脚本根目录，避免跨模块强依赖 DB 配置。"""
    env_dir = os.environ.get("ECOM_RUNTIME_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local/share/ecom-agent"


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
        商品列表，包含 ``title``, ``price``, ``sales``, ``shop``, ``url`` 等；失败时返回空列表 ``[]``。
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
                return resp.json().get("items", [])[:limit]
        except Exception as exc:
            _LOG.warning("1688 外部检索网关调用失败，尝试本地抓手: %s", exc)

    runtime_dir = _resolve_runtime_dir()
    py = runtime_dir / "scraper-venv/bin/python"
    script = runtime_dir / "p1688_mcp/p1688_search.py"
    if py.exists() and script.exists():
        try:
            proc = subprocess.run(
                [str(py), str(script), keyword, "--limit", str(limit)],
                capture_output=True,
                text=True,
                timeout=min(90.0, budget),
            )
            for line in reversed((proc.stdout or "").strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    res = json.loads(line)
                    if res.get("state") == "ok":
                        return res.get("items", [])[:limit]
        except Exception as exc:
            _LOG.warning("1688 本地抓手执行异常: %s", exc)

    return []
