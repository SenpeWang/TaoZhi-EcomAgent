"""ChatBI 维度与语义层指标数据库仓储。"""
from __future__ import annotations

import threading
import time
from typing import Any

from taozhi.knowledge.db import fetch_all, fetch_one

_CHANNEL_ALIASES = {"淘宝": "天猫", "小程序": "微信小程序", "微信小店": "微信小程序"}
_BI_DIM_CACHE: dict[str, tuple[float, dict[str, str], list[str]]] = {}
_BI_DIM_LOCK = threading.Lock()
_BI_DIM_TTL_S = 60.0


def load_bi_dimensions(conn: Any, tenant_id: str) -> tuple[dict[str, str], list[str]]:
    """从数据库动态加载渠道名与商品品类列表（带 60s 进程缓存）。"""
    now = time.monotonic()
    with _BI_DIM_LOCK:
        cached = _BI_DIM_CACHE.get(tenant_id)
        if cached and now - cached[0] < _BI_DIM_TTL_S:
            return cached[1], cached[2]

    channels: dict[str, str] = {}
    categories: list[str] = []
    try:
        ch_rows = fetch_all(conn, "SELECT DISTINCT name FROM channels WHERE tenant_id=%s", (tenant_id,))
        for r in ch_rows:
            name = str(r.get("name") or "").strip()
            if name:
                channels[name] = name
        for alias, target in _CHANNEL_ALIASES.items():
            if target in channels and alias not in channels:
                channels[alias] = target
        cat_rows = fetch_all(conn, "SELECT DISTINCT category FROM products WHERE tenant_id=%s", (tenant_id,))
        categories = sorted(
            {str(r.get("category") or "").strip() for r in cat_rows if str(r.get("category") or "").strip()},
            key=len,
            reverse=True,
        )
    except Exception:
        pass

    if channels or categories:
        with _BI_DIM_LOCK:
            _BI_DIM_CACHE[tenant_id] = (now, channels, categories)
    return channels, categories


def list_published_metrics(conn: Any, tenant_id: str) -> list[dict]:
    """读取租户下所有已发布语义指标。"""
    return fetch_all(conn, "SELECT * FROM metrics WHERE tenant_id=%s AND state='published'", (tenant_id,))


def get_published_metric_by_id(conn: Any, tenant_id: str, metric_id: str) -> dict | None:
    """按指标 ID 读取已发布指标。"""
    return fetch_one(
        conn,
        "SELECT * FROM metrics WHERE id=%s AND tenant_id=%s AND state='published'",
        (metric_id, tenant_id),
    )


def get_published_metric_by_key(conn: Any, tenant_id: str, metric_key: str) -> dict | None:
    """按指标 Key 读取已发布指标。"""
    return fetch_one(
        conn,
        "SELECT * FROM metrics WHERE metric_key=%s AND tenant_id=%s AND state='published'",
        (metric_key, tenant_id),
    )


def get_latest_order_at(conn: Any, tenant_id: str) -> str | None:
    """获取最近一笔订单创建时间 ISO 字符串。"""
    row = fetch_one(conn, "SELECT max(created_at) AS latest_at FROM orders WHERE tenant_id=%s", (tenant_id,))
    return row["latest_at"].isoformat() if row and row["latest_at"] else None
