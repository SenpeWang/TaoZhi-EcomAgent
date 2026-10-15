"""业务指标注册（数据中台语义层）：销售 / 售后 / 库存 / 成本四域。

用法：ECOM_ENV_FILE=.env.production .venv/bin/python scripts/seed_business_metrics.py

口径约定（metrics.py 安全边界）：
- 模板 = 内层明细子查询（输出列带别名）+ 外层 WHERE 1=1 %(filters)s + GROUP BY 聚合；
  维度过滤列必须是内层输出的别名，保证过滤器只落在白名单列上；
- 必含 %(tenant_id)s；可含 %(since)s/%(until)s/%(filters)s；禁分号/注释/LIMIT/字面量 %。
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_copilot.enterprise.db import database_connection, fetch_one  # noqa: E402

# (key, 名称, 别名, 模板, 维度, 主表, 单位, 格式, 密级, scope, 归属节点, 说明)
METRICS = [
    ("biz.gsv", "销售额",
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
     ["channel_name", "category"], "orders", "元", "currency", 1, "company", "root",
     "统计周期内有效订单（不含取消）的销售金额，按渠道与品类分列。"),

    ("biz.gsv_daily", "日销售额",
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
     ["channel_name", "category"], "orders", "元", "currency", 1, "company", "root",
     "按自然日聚合的销售额时间序列，支持渠道/品类过滤，供趋势图使用。"),

    ("biz.order_count", "订单量",
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
     ["channel_name", "category", "status"], "orders", "单", "number", 1, "company", "root",
     "统计周期内订单数，按渠道、品类、状态分列（含取消与退款单）。"),

    ("biz.refund_rate", "退货退款率",
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
     ["channel_name"], "orders", "%", "percent", 1, "company", "root",
     "有效订单中产生售后工单的比例，按渠道分列——售后质量的核心监控指标。"),

    ("biz.aftersales_count", "售后工单量",
     ["售后工单", "售后量", "售后多少单", "退换货数量"],
     """SELECT type, reason, count(*) AS value
        FROM (SELECT a.type AS type, a.reason AS reason
              FROM after_sales a
              WHERE a.tenant_id=%(tenant_id)s
              AND a.created_at >= %(since)s AND a.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
     ["type", "reason"], "after_sales", "单", "number", 1, "company", "root",
     "统计周期内售后工单数，按类型（退款/退货/换新）与原因分列。"),

    ("biz.gross_profit", "毛利",
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
     ["channel_name", "category"], "orders", "元", "currency", 2, "org", "电商运营部",
     "销售额减去商品成本（未扣营销与履约费用），按渠道与品类分列。二级机密：成本敏感。"),

    ("biz.refund_amount", "退款金额",
     ["退款金额", "退了多少钱", "退款额"],
     """SELECT type, reason, round(sum(amount)/100.0, 2) AS value
        FROM (SELECT a.type AS type, a.reason AS reason, a.amount_minor AS amount
              FROM after_sales a
              WHERE a.tenant_id=%(tenant_id)s
              AND a.created_at >= %(since)s AND a.created_at < %(until)s) base
        WHERE 1=1 %(filters)s
        GROUP BY 1, 2 ORDER BY 3 DESC""",
     ["type", "reason"], "after_sales", "元", "currency", 2, "org", "电商运营部",
     "统计周期内售后工单涉及金额，按类型与原因分列。二级机密：财务敏感。"),

    ("biz.low_stock_count", "低库存SKU数",
     ["低库存", "缺货", "库存预警", "哪些要补货", "低于安全库存"],
     """SELECT warehouse, count(*) AS value
        FROM (SELECT st.warehouse AS warehouse, s.sku_code AS sku_code,
                     st.qty AS qty, st.safety_qty AS safety_qty
              FROM stock st
              JOIN skus s ON s.id=st.sku_id AND s.tenant_id=st.tenant_id
              WHERE st.tenant_id=%(tenant_id)s AND st.qty < st.safety_qty) base
        WHERE 1=1 %(filters)s
        GROUP BY 1 ORDER BY 2 DESC""",
     ["warehouse"], "stock", "个", "number", 2, "org", "电商运营部",
     "当前库存低于安全线的 SKU 数，按仓库分列——补货预警信号。"),

    ("biz.procurement_cost", "采购成本",
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
     ["category"], "orders", "元", "currency", 3, "company", "root",
     "统计周期内售出商品的进货成本合计。三级机密：供应商底价，仅老板可见。"),
]


def main() -> int:
    with database_connection() as c:
        tenant = fetch_one(c, "SELECT id FROM tenants ORDER BY created_at LIMIT 1")
        if not tenant:
            print("未找到租户，请先初始化企业库")
            return 1
        tid = tenant["id"]
        boss = fetch_one(c, """SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id
            WHERE u.tenant_id=%s AND m.role='boss' AND m.active AND u.active ORDER BY u.created_at LIMIT 1""", (tid,))
        root = fetch_one(c, "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind='company'", (tid,))
        if not boss or not root:
            print("未找到老板账号或公司根节点，请先 bootstrap-owner")
            return 1
        nodes = {r["name"]: r["id"] for r in c.execute(
            "SELECT name, id FROM org_nodes WHERE tenant_id=%s", (tid,)).fetchall()}
        n = 0
        for key, name, aliases, tpl, dims, table, unit, fmt, level, scope, node_name, desc in METRICS:
            node_id = root["id"] if node_name == "root" else nodes.get(node_name)
            if node_name != "root" and not node_id:
                print(f"跳过 {key}：找不到组织节点 {node_name}")
                continue
            mid = "met_" + hashlib.sha256(f"{tid}:{key}".encode()).hexdigest()[:24]
            c.execute("""INSERT INTO metrics(id,tenant_id,metric_key,name,aliases,description,sql_template,
                  dimensions,base_table,unit,format,level,scope,node_id,owner_id,state)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'published')
                ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,aliases=EXCLUDED.aliases,
                  description=EXCLUDED.description,sql_template=EXCLUDED.sql_template,
                  dimensions=EXCLUDED.dimensions,base_table=EXCLUDED.base_table,unit=EXCLUDED.unit,
                  format=EXCLUDED.format,level=EXCLUDED.level,scope=EXCLUDED.scope,
                  node_id=EXCLUDED.node_id,state='published',updated_at=now()""",
                (mid, tid, key, name, aliases, desc, tpl, dims, table, unit, fmt, level, scope, node_id, boss["id"]))
            n += 1
        print(f"已注册/更新 {n} 个业务指标（负责人=老板；L2 归属电商运营部，其余归属公司根节点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
