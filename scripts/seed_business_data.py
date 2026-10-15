"""业务合成数据生成器（确定性、可复现、来源可审计）。

用法：ECOM_ENV_FILE=.env.production .venv/bin/python scripts/seed_business_data.py

设计原则（生产级学习项目）：
- 固定随机种子 SEED，同一环境重跑结果完全一致；
- 数据形态贴近真实手机配件电商：渠道权重、周末效应、618/国庆大促脉冲、
  售后原因分布，并埋入一个可归因的异常钩子（拼多多渠道钢化膜 9/20 后退货率异常，
  供 ChatBI 归因分析演示）；
- 金额一律 minor units（分）；只清空本组合成表（按租户），不触碰工作台自身数据。
"""
import hashlib
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_copilot.enterprise.db import database_connection, fetch_one  # noqa: E402

SEED = 20261015
DAYS_BACK = 183          # 2026-04-15 起
BASE_DAILY = 52          # 工作日基础单量

CHANNELS = [  # (code, name, 权重)
    ("tmall", "天猫", 0.35), ("jd", "京东", 0.25), ("pdd", "拼多多", 0.20),
    ("douyin", "抖音", 0.15), ("wxapp", "微信小程序", 0.05),
]
PRODUCTS = [  # (spu_code, name, category, cost分, spec→[(规格, 售价分)])
    ("SPU-MEM-IP15", "iPhone 15 系列高清钢化膜", "钢化膜", 850,
     [("黑边全覆盖", 3990), ("高清裸边", 3690)]),
    ("SPU-MEM-IP15PM", "iPhone 15 Pro Max 防窥膜", "钢化膜", 1200, [("防窥版", 4990)]),
    ("SPU-CASE-M60", "Mate 60 磁吸保护壳", "手机壳", 1500, [("曜金黑", 6900), ("釉白色", 6900)]),
    ("SPU-CASE-UNI", "通用液态硅胶手机壳", "手机壳", 900,
     [("黑色", 3900), ("雾蓝", 3900), ("樱粉", 3900)]),
    ("SPU-PAD-IPPRO", "iPad Pro 类纸膜", "平板膜", 1800, [("12.9 英寸", 7900), ("11 英寸", 7900)]),
    ("SPU-CHG-65W", "65W 氮化镓充电器", "充电器", 3800, [("单口", 12900), ("双口", 14900)]),
    ("SPU-CBL-C240", "Type-C 240W 编织数据线", "数据线", 650, [("1 米", 2500), ("2 米", 2900)]),
    ("SPU-HDR-CAR", "磁吸车载支架", "车载支架", 1400, [("出风口版", 5900)]),
]
STATUS_W = [("pending", .02), ("paid", .04), ("shipped", .05), ("delivered", .07),
            ("completed", .78), ("cancelled", .04)]
REFUND_BASE = 0.055                       # 全局退款改写率
ANOMALY = dict(channel="pdd", spu="SPU-MEM-IP15", since="2026-09-20", rate=0.30)
AS_REASONS = ["质量问题", "尺寸不符", "物流破损", "七天无理由", "描述不符"]
AS_REASONS_ANOMALY = ["质量问题", "质量问题", "质量问题", "物流破损", "七天无理由"]  # 异常期偏置
AS_TYPE_BY = {"质量问题": ("return", "refund"), "尺寸不符": ("exchange", "refund"),
              "物流破损": ("refund", "return"), "七天无理由": ("refund", "return"),
              "描述不符": ("refund", "exchange")}
LOW_STOCK = {  # sku_code → (warehouse, qty, safety_qty) 低库存预警演示
    "SPU-CHG-65W/双口": ("华东仓", 120, 300),
    "SPU-CBL-C240/1 米": ("华南仓", 80, 250),
    "SPU-MEM-IP15PM/防窥版": ("华东仓", 45, 200),
}


def _id(prefix: str, seq: int) -> str:
    return prefix + "_" + hashlib.sha256(f"seed:{seq}".encode()).hexdigest()[:24]


def bulk(c, sql: str, rows: list, batch: int = 500) -> None:
    with c.cursor() as cur:
        for i in range(0, len(rows), batch):
            cur.executemany(sql, rows[i:i + batch])


def _promo_factor(day: datetime) -> float:
    md = (day.month, day.day)
    if (6, 12) <= md <= (6, 18):
        return 2.6          # 618
    if (10, 1) <= md <= (10, 6):
        return 1.7          # 国庆
    if (8, 25) <= md <= (9, 5):
        return 1.35         # 开学季
    return 1.25 if day.weekday() >= 5 else 1.0


def main() -> int:
    rng = random.Random(SEED)
    with database_connection() as c:
        tenant = fetch_one(c, "SELECT id FROM tenants ORDER BY created_at LIMIT 1")
        if not tenant:
            print("未找到租户")
            return 1
        tid = tenant["id"]
        owner_ids = [r["id"] for r in c.execute(
            "SELECT id FROM users WHERE tenant_id=%s AND username IN ('leader','staff')", (tid,)).fetchall()]
        node_ids = [r["id"] for r in c.execute(
            "SELECT id FROM org_nodes WHERE tenant_id=%s AND name IN ('店铺运营组','售前客服组')", (tid,)).fetchall()]
        if not owner_ids or not node_ids:
            print("缺用户/组织节点，请先初始化组织架构")
            return 1

        # 幂等：只清合成表（FK 依赖序）
        for stmt in ("DELETE FROM after_sales WHERE tenant_id=%s", "DELETE FROM orders WHERE tenant_id=%s",
                     "DELETE FROM stock WHERE tenant_id=%s", "DELETE FROM skus WHERE tenant_id=%s",
                     "DELETE FROM products WHERE tenant_id=%s", "DELETE FROM channels WHERE tenant_id=%s"):
            c.execute(stmt, (tid,))

        # 渠道 / SPU / SKU
        ch_rows, sku_index = [], {}
        for i, (code, name, _) in enumerate(CHANNELS):
            ch_rows.append((_id("ch", i), tid, code, name))
        bulk(c, "INSERT INTO channels(id,tenant_id,code,name) VALUES(%s,%s,%s,%s)", ch_rows)
        ch_ids = {r[2]: r[0] for r in ch_rows}

        sku_rows, seq = [], 0
        for spu_code, spu_name, cat, cost, specs in PRODUCTS:
            spu_id = _id("spu", seq); seq += 1
            c.execute("""INSERT INTO products(id,tenant_id,spu_code,name,category,cost_minor,list_price_minor)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                      (spu_id, tid, spu_code, spu_name, cat, cost, max(p for _, p in specs)))
            for spec, price in specs:
                sku_id = _id("sku", seq); seq += 1
                sku_rows.append((sku_id, tid, spu_id, f"{spu_code}/{spec}", spec, price, cost))
                sku_index[f"{spu_code}/{spec}"] = dict(id=sku_id, spu=spu_code, cat=cat,
                                                       price=price, cost=cost, spu_name=spu_name)
        bulk(c, "INSERT INTO skus(id,tenant_id,spu_id,sku_code,spec,price_minor,cost_minor) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)", sku_rows)

        # 订单流水
        start = datetime(2026, 4, 15, tzinfo=timezone.utc)
        codes, weights = [x[0] for x in CHANNELS], [x[2] for x in CHANNELS]
        o_rows, as_rows, seq, order_no_n = [], [], 0, 0
        for d in range(DAYS_BACK):
            day = start + timedelta(days=d)
            for _ in range(int(BASE_DAILY * _promo_factor(day) * (1 + rng.uniform(-0.12, 0.12)))):
                seq += 1
                ch = rng.choices(codes, weights=weights)[0]
                sku = sku_index[rng.choice(list(sku_index))]
                qty = rng.choices([1, 2, 3], weights=[.85, .12, .03])[0]
                amount, cost = sku["price"] * qty, sku["cost"] * qty
                st = rng.choices([s for s, _ in STATUS_W], weights=[w for _, w in STATUS_W])[0]
                ts = day.replace(hour=rng.randint(8, 23), minute=rng.randint(0, 59),
                                 second=rng.randint(0, 59), microsecond=0)
                # 异常钩子：9/20 起拼多多钢化膜退款率飙升（批次质量）
                is_anom = (ch == ANOMALY["channel"] and sku["spu"] == ANOMALY["spu"]
                           and ts >= datetime(2026, 9, 20, tzinfo=timezone.utc))
                if st in ("shipped", "delivered", "completed") and (
                        rng.random() < (ANOMALY["rate"] if is_anom else REFUND_BASE)):
                    st = "refunded"
                oid = _id("ord", seq)
                order_no_n += 1
                paid_at = ts if st != "pending" else None
                o_rows.append((oid, tid, f"TD{ts:%Y%m%d}{order_no_n:05d}", ch_ids[ch], sku["id"],
                               qty, amount, cost, st, rng.choice(node_ids), rng.choice(owner_ids),
                               ts, paid_at))
                if st == "refunded":
                    reasons = AS_REASONS_ANOMALY if is_anom else AS_REASONS
                    reason = rng.choice(reasons)
                    at = rng.choices(AS_TYPE_BY[reason], weights=[.6, .4])[0]
                    ast = rng.choices(["resolved", "pending", "approved", "rejected"],
                                      weights=[.82, .10, .05, .03])[0]
                    res = ts + timedelta(days=rng.randint(1, 7)) if ast == "resolved" else None
                    as_rows.append((_id("as", len(as_rows) + 1), tid, oid, at, reason, amount, ast, ts, res))

        bulk(c, """INSERT INTO orders(id,tenant_id,order_no,channel_id,sku_id,qty,amount_minor,
              cost_minor,status,node_id,owner_id,created_at,paid_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", o_rows)
        bulk(c, """INSERT INTO after_sales(id,tenant_id,order_id,type,reason,amount_minor,
              status,created_at,resolved_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", as_rows)

        # 库存快照
        st_rows = []
        for code, sku in sku_index.items():
            for wh in ("华东仓", "华南仓"):
                low = LOW_STOCK.get(code)
                qty, safety = (low[1], low[2]) if low and low[0] == wh else \
                    (rng.randint(200, 3000), 300)
                st_rows.append((tid, sku["id"], wh, qty, safety))
        bulk(c, "INSERT INTO stock(tenant_id,sku_id,warehouse,qty,safety_qty) VALUES(%s,%s,%s,%s,%s)",
             st_rows)
        c.execute("ANALYZE orders, after_sales, stock")

        # 统计输出
        gmv = sum(r["amount_minor"] for r in c.execute(
            "SELECT amount_minor FROM orders WHERE tenant_id=%s AND status<>'cancelled'", (tid,)).fetchall())
        n_orders = c.execute("SELECT count(*) AS n FROM orders WHERE tenant_id=%s", (tid,)).fetchone()["n"]
        n_as = c.execute("SELECT count(*) AS n FROM after_sales WHERE tenant_id=%s", (tid,)).fetchone()["n"]
        anom = c.execute("""SELECT count(*) FILTER (WHERE o.status='refunded') AS refunded, count(*) AS total
              FROM orders o JOIN skus s ON s.id=o.sku_id JOIN channels c2 ON c2.id=o.channel_id
              WHERE o.tenant_id=%s AND c2.code='pdd' AND s.sku_code LIKE 'SPU-MEM-IP15/%%'
              AND o.created_at >= '2026-09-20'""", (tid,)).fetchone()
        print(f"合成数据完成：SKU {len(sku_rows)} / 订单 {n_orders}（GMV ¥{gmv/100:,.0f}）/ 售后 {n_as}")
        print(f"异常钩子：pdd 钢化膜 9/20 后退款单 {anom['refunded']}/{anom['total']} = "
              f"{100*anom['refunded']/max(1,anom['total']):.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
