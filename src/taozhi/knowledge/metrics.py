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

from ..config import load_config
from ..knowledge.db import fetch_all
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence.access_repo import can_read_metric

# 内置企业电商核心业务指标目录（供初始化脚本与评测夹具共用单一事实来源）
# tuple 结构：(metric_key, 名称, 别名, SQL模板, 维度白名单, 主表, 单位, 格式, 密级, scope, 归属节点名, 说明)
BUILTIN_BUSINESS_METRICS: tuple[tuple, ...] = (
    (
        "biz.gsv",
        "销售额",
        ["销售额", "GMV", "营业额", "卖了多少钱", "卖了多少", "成交金额"],
        """SELECT channel_name, category, round(sum(amount)/100.0, 2) AS value
        FROM (SELECT c.name AS channel_name, p.category AS category, o.amount_minor AS amount
              FROM orders o
              JOIN channels c ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
              JOIN skus s ON s.id=o.sku_id AND s.tenant_id=o.tenant_id
              JOIN products p ON p.id=s.spu_id AND p.tenant_id=s.tenant_id
              WHERE o.tenant_id=%(tenant_id)s AND o.status <> 'cancelled'
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
        ["channel_name", "category"],
        "orders",
        "元",
        "currency",
        1,
        "company",
        "root",
        "统计周期内有效订单（不含取消）的销售金额，按渠道与品类分列。",
    ),
    (
        "biz.gsv_daily",
        "日销售额",
        ["日销售额", "每天卖多少", "销售趋势", "每日成交"],
        """SELECT day, round(sum(amount)/100.0, 2) AS value
        FROM (SELECT date_trunc('day', o.created_at)::date AS day, o.amount_minor AS amount,
                     c.name AS channel_name, p.category AS category
              FROM orders o
              JOIN channels c ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
              JOIN skus s ON s.id=o.sku_id AND s.tenant_id=o.tenant_id
              JOIN products p ON p.id=s.spu_id AND p.tenant_id=s.tenant_id
              WHERE o.tenant_id=%(tenant_id)s AND o.status <> 'cancelled'
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1 ORDER BY 1""",
        ["channel_name", "category"],
        "orders",
        "元",
        "currency",
        1,
        "company",
        "root",
        "按自然日聚合的销售额时间序列，支持渠道/品类过滤，供趋势图使用。",
    ),
    (
        "biz.order_count",
        "订单量",
        ["订单量", "多少订单", "单量", "订单数"],
        """SELECT channel_name, category, status, count(*) AS value
        FROM (SELECT c.name AS channel_name, p.category AS category, o.status AS status
              FROM orders o
              JOIN channels c ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
              JOIN skus s ON s.id=o.sku_id AND s.tenant_id=o.tenant_id
              JOIN products p ON p.id=s.spu_id AND p.tenant_id=s.tenant_id
              WHERE o.tenant_id=%(tenant_id)s
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2, 3 ORDER BY 4 DESC""",
        ["channel_name", "category", "status"],
        "orders",
        "单",
        "number",
        1,
        "company",
        "root",
        "统计周期内订单数，按渠道、品类、状态分列（含取消与退款单）。",
    ),
    (
        "biz.refund_rate",
        "退货退款率",
        ["退货率", "退款率", "售后率", "退单比例"],
        """SELECT channel_name, round(100.0 * sum(refunded) / NULLIF(count(*), 0), 2) AS value
        FROM (SELECT c.name AS channel_name,
                     CASE WHEN EXISTS(SELECT 1 FROM after_sales a
                        WHERE a.tenant_id=o.tenant_id AND a.order_id=o.id) THEN 1 ELSE 0 END AS refunded
              FROM orders o
              JOIN channels c ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
              WHERE o.tenant_id=%(tenant_id)s AND o.status <> 'cancelled'
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1 ORDER BY 2 DESC""",
        ["channel_name"],
        "orders",
        "%",
        "percent",
        1,
        "company",
        "root",
        "有效订单中产生售后工单的比例，按渠道分列——售后质量的核心监控指标。",
    ),
    (
        "biz.aftersales_count",
        "售后工单量",
        ["售后工单", "售后量", "售后多少单", "退换货数量"],
        """SELECT type, reason, count(*) AS value
        FROM (SELECT a.type AS type, a.reason AS reason
              FROM after_sales a
              WHERE a.tenant_id=%(tenant_id)s
              AND a.created_at >= %(since)s AND a.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
        ["type", "reason"],
        "after_sales",
        "单",
        "number",
        1,
        "company",
        "root",
        "统计周期内售后工单数，按类型（退款/退货/换新）与原因分列。",
    ),
    (
        "biz.gross_profit",
        "毛利",
        ["毛利", "赚了多少", "利润", "毛利润"],
        """SELECT channel_name, category, round(sum(amount - cost)/100.0, 2) AS value
        FROM (SELECT c.name AS channel_name, p.category AS category,
                     o.amount_minor AS amount, o.cost_minor AS cost
              FROM orders o
              JOIN channels c ON c.id=o.channel_id AND c.tenant_id=o.tenant_id
              JOIN skus s ON s.id=o.sku_id AND s.tenant_id=o.tenant_id
              JOIN products p ON p.id=s.spu_id AND p.tenant_id=s.tenant_id
              WHERE o.tenant_id=%(tenant_id)s AND o.status <> 'cancelled'
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
        ["channel_name", "category"],
        "orders",
        "元",
        "currency",
        2,
        "org",
        "电商运营部",
        "销售额减去商品成本（未扣营销与履约费用），按渠道与品类分列。二级机密：成本敏感。",
    ),
    (
        "biz.refund_amount",
        "退款金额",
        ["退款金额", "退了多少钱", "退款额"],
        """SELECT type, reason, round(sum(amount)/100.0, 2) AS value
        FROM (SELECT a.type AS type, a.reason AS reason, a.amount_minor AS amount
              FROM after_sales a
              WHERE a.tenant_id=%(tenant_id)s
              AND a.created_at >= %(since)s AND a.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
        ["type", "reason"],
        "after_sales",
        "元",
        "currency",
        2,
        "org",
        "电商运营部",
        "统计周期内售后工单涉及金额，按类型与原因分列。二级机密：财务敏感。",
    ),
    (
        "biz.low_stock_count",
        "低库存SKU数",
        ["低库存", "缺货", "库存预警", "哪些要补货", "低于安全库存"],
        """SELECT warehouse, count(*) AS value
        FROM (SELECT st.warehouse AS warehouse, s.sku_code AS sku_code,
                     st.qty AS qty, st.safety_qty AS safety_qty
              FROM stock st
              JOIN skus s ON s.id=st.sku_id AND s.tenant_id=st.tenant_id
              WHERE st.tenant_id=%(tenant_id)s AND st.qty < st.safety_qty) base
        WHERE 1=1 %(filters)s
        GROUP BY 1 ORDER BY 2 DESC""",
        ["warehouse"],
        "stock",
        "个",
        "number",
        2,
        "org",
        "电商运营部",
        "当前库存低于安全线的 SKU 数，按仓库分列——补货预警信号。",
    ),
    (
        "biz.procurement_cost",
        "采购成本",
        ["采购成本", "进货成本", "底价", "成本多少", "进货额"],
        """SELECT category, round(sum(cost)/100.0, 2) AS value
        FROM (SELECT p.category AS category, o.cost_minor AS cost
              FROM orders o
              JOIN skus s ON s.id=o.sku_id AND s.tenant_id=o.tenant_id
              JOIN products p ON p.id=s.spu_id AND p.tenant_id=s.tenant_id
              WHERE o.tenant_id=%(tenant_id)s AND o.status <> 'cancelled'
              AND o.created_at >= %(since)s AND o.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1 ORDER BY 2 DESC""",
        ["category"],
        "orders",
        "元",
        "currency",
        3,
        "company",
        "root",
        "统计周期内售出商品的进货成本合计。三级机密：供应商底价，仅老板可见。",
    ),
)

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


def list_metrics(conn, p):
    """列出当前主体有权查询的发布态指标。"""
    rows = fetch_all(conn, "SELECT * FROM metrics WHERE tenant_id=%s AND state='published' ORDER BY metric_key", (p.tenant_id,))
    return [m for m in rows if can_read_metric(conn, p, m)]


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
