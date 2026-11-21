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

from taozhi.knowledge.db import database_connection, fetch_one
from taozhi.knowledge.metrics import BUILTIN_BUSINESS_METRICS


def main() -> int:
    """将内置企业电商业务指标目录幂等写入当前租户语义层表。"""
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
        for key, name, aliases, tpl, dims, table, unit, fmt, level, scope, node_name, desc in BUILTIN_BUSINESS_METRICS:
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
