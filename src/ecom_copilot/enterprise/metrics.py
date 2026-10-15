"""指标语义层：定义、授权、匹配与受控执行（ChatBI 的安全底座）。

安全边界（生产红线，任何修改不得绕过）：
- SQL 永不由 LLM 生成；LLM 只输出结构化意图（metric_key + 维度 + 过滤 + 时间范围）；
- SQL 模板由指标负责人编写（可信资产，等同代码），执行器只做白名列填充与参数绑定；
- 模板必须包含 %(tenant_id)s 占位符（缺失即拒绝，防跨租户）；
- 维度列必须在指标 dimensions 白名单内；主表必须在 ALLOWED_METRIC_TABLES 内；
- 独立只读事务 + statement_timeout + 行数上限；字面量 % 必须写成 %%。
"""
from __future__ import annotations

import re
import time

import numpy as np

from .config import load_config
from .db import database_connection, fetch_all, fetch_one
from .policy import Denied
from .retriever import words

# 指标 SQL 只允许访问的表（企业工作台自身表 + 业务系统表，接入时在此登记）
ALLOWED_METRIC_TABLES = frozenset({
    "tenants", "users", "memberships", "org_nodes", "sessions", "login_attempts",
    "documents", "document_versions", "chunks", "knowledge_mentions", "document_grants",
    "jobs", "task_events", "task_reviews", "task_outbox", "approvals",
    "model_calls", "audit_events", "worker_heartbeats", "metrics", "metric_grants",
    # 业务数据表（合成学习数据，真实业务系统接入时同构对接）
    "products", "skus", "channels", "orders", "after_sales", "stock",
})
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 5000
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_metric_grant(conn, p, m):
    return fetch_one(conn, "SELECT * FROM metric_grants WHERE metric_id=%s AND user_id=%s", (m["id"], p.id))


def can_read_metric(conn, p, m) -> bool:
    """指标可见性：与文档同构——租户 + 发布状态 + deny 优先 + allow 例外 + 老板全通 + 密级/组织 grade。"""
    if m["tenant_id"] != p.tenant_id or m["state"] != "published":
        return False
    g = get_metric_grant(conn, p, m)
    if g and g["effect"] == "deny":
        return False
    if g and g["effect"] == "allow":
        return True
    if p.boss:
        return True
    return m["scope"] != "selected" and p.grade(m["node_id"], m["scope"]) >= m["level"]


def list_metrics(conn, p):
    rows = fetch_all(conn, "SELECT * FROM metrics WHERE tenant_id=%s AND state='published' ORDER BY metric_key", (p.tenant_id,))
    return [m for m in rows if can_read_metric(conn, p, m)]


def search_metrics(conn, p, query, limit=5):
    """按 key/名称/别名/说明匹配指标（jieba 词重叠 + 精确命中加权），权限前置过滤。"""
    terms = set(words(query))
    q = (query or "").strip().lower()
    scored = []
    for m in list_metrics(conn, p):
        toks = set(words(m["metric_key"] + " " + m["name"] + " " + " ".join(m["aliases"]) + " " + m["description"]))
        score = len(terms & toks) / max(1, len(terms))
        if q and (q == m["metric_key"].lower() or q in [a.lower() for a in m["aliases"]]):
            score += 1.0
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:limit]]


def _validate_template(m) -> None:
    t = m["sql_template"]
    if "%(tenant_id)s" not in t:
        raise Denied("指标口径缺少租户占位符，已拒绝执行", "METRIC_TEMPLATE", 500)
    if ";" in t or "--" in t or "/*" in t:
        raise Denied("指标口径含禁用字符，已拒绝执行", "METRIC_TEMPLATE", 500)
    if re.search(r"\bLIMIT\b", t, re.I):
        raise Denied("指标口径不得自带 LIMIT（由执行器统一追加）", "METRIC_TEMPLATE", 500)
    if m["base_table"] not in ALLOWED_METRIC_TABLES:
        raise Denied("指标主表不在白名单内", "METRIC_TABLE", 500)


def _fill_filters(m, intent) -> tuple:
    """把意图里的过滤条件编译成 SQL 片段与参数；列名必须在 dimensions 白名单内。"""
    filters = intent.get("filters") or {}
    if not isinstance(filters, dict) or len(filters) > 5:
        raise Denied("过滤条件无效", "METRIC_INTENT", 400)
    dims = set(m["dimensions"])
    clauses, params = [], {}
    for i, (col, val) in enumerate(filters.items()):
        if col not in dims or not _IDENT.match(col):
            raise Denied(f"维度 {col} 不在指标白名单内", "METRIC_DIMENSION", 400)
        if val is None or isinstance(val, (dict, list)) or (isinstance(val, str) and len(val) > 64):
            raise Denied("过滤值无效", "METRIC_INTENT", 400)
        clauses.append(f'AND "{col}" = %(f{i})s')
        params[f"f{i}"] = val
    return (" ".join(clauses), params) if clauses else ("", {})


def execute_metric(conn, p, m, intent, max_rows: int = MAX_ROWS):
    """受控执行：模板填充 + 只读事务 + 超时 + 行数上限。返回 (columns, rows, meta)。"""
    _validate_template(m)
    if intent.get("metric_key") and intent["metric_key"] != m["metric_key"]:
        raise Denied("意图与指标不匹配", "METRIC_INTENT", 400)
    frag, fparams = _fill_filters(m, intent)
    sql = m["sql_template"].replace("%(filters)s", frag)
    params = dict(fparams)
    params["tenant_id"] = p.tenant_id
    # 时间范围：模板引用 %(since)s/%(until)s 时由意图提供，缺省近 30 天
    tr = intent.get("time_range") or {}
    days = tr.get("days") if isinstance(tr, dict) else None
    if "%(since)s" in sql:
        params["since"] = _iso(tr.get("since"), days or 30)
    if "%(until)s" in sql:
        params["until"] = _iso(tr.get("until"), None) or _now_iso()
    sql = f"SELECT * FROM ({sql}) metric_result LIMIT {min(int(max_rows), MAX_ROWS)}"
    started = time.monotonic()
    import psycopg
    with psycopg.connect(load_config().dsn, connect_timeout=8) as ro:  # 独立连接，只读事务
        with ro.transaction():
            ro.execute("SET TRANSACTION READ ONLY")
            ro.execute(f"SET LOCAL statement_timeout={STATEMENT_TIMEOUT_MS}")
            cur = ro.execute(sql, params)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall()
    meta = dict(metric_key=m["metric_key"], name=m["name"], unit=m["unit"], format=m["format"],
                dimensions=m["dimensions"], base_table=m["base_table"], level=m["level"],
                elapsed_ms=int((time.monotonic() - started) * 1000), row_count=len(rows))
    return cols, rows, meta


def jsonable_rows(rows):
    """指标行 JSON 化（Decimal→float、date/datetime→isoformat），供 API/前端图表消费。"""
    from decimal import Decimal
    def _one(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
    return [[_one(v) for v in r] for r in rows]


def _iso(value, default_days):
    from datetime import datetime, timedelta, timezone
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise Denied("时间格式无效", "METRIC_INTENT", 400)
    if default_days is None:
        return None  # until 场景：无显式终点时由调用方回退到 now
    return datetime.now(timezone.utc) - timedelta(days=int(default_days))


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
