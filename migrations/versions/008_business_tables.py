"""业务数据表：商品/ SKU / 渠道 / 订单 / 售后 / 库存（数据中台取数底座）。

说明：本组表承载合成学习数据（scripts/seed_business_data.py 确定性生成、可复现），
工程质量与授权体系按生产级建设；真实业务系统接入时以同构表结构对接。
"""
from alembic import op

revision="008_business_tables"
down_revision="007_metrics"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("COMMENT ON TABLE metrics IS '指标语义层（生产级框架）'")
    op.execute("""CREATE TABLE products(
 id text PRIMARY KEY, tenant_id text NOT NULL, spu_code text NOT NULL,
 name text NOT NULL, category text NOT NULL, brand text NOT NULL DEFAULT '膜法实验室',
 cost_minor bigint NOT NULL CHECK(cost_minor>=0), list_price_minor bigint NOT NULL CHECK(list_price_minor>=0),
 state text NOT NULL CHECK(state IN ('active','disabled')) DEFAULT 'active',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,spu_code),
 FOREIGN KEY(tenant_id) REFERENCES tenants(id))""")
    op.execute("""CREATE TABLE skus(
 id text PRIMARY KEY, tenant_id text NOT NULL, spu_id text NOT NULL, sku_code text NOT NULL,
 spec text NOT NULL, price_minor bigint NOT NULL CHECK(price_minor>=0),
 cost_minor bigint NOT NULL CHECK(cost_minor>=0),
 state text NOT NULL CHECK(state IN ('active','disabled')) DEFAULT 'active',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,sku_code),
 FOREIGN KEY(tenant_id,spu_id) REFERENCES products(tenant_id,id))""")
    op.execute("""CREATE TABLE channels(
 id text PRIMARY KEY, tenant_id text NOT NULL, code text NOT NULL, name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,code),
 FOREIGN KEY(tenant_id) REFERENCES tenants(id))""")
    op.execute("""CREATE TABLE orders(
 id text PRIMARY KEY, tenant_id text NOT NULL, order_no text NOT NULL, channel_id text NOT NULL,
 sku_id text NOT NULL, qty integer NOT NULL CHECK(qty>0), amount_minor bigint NOT NULL CHECK(amount_minor>=0),
 cost_minor bigint NOT NULL CHECK(cost_minor>=0),
 status text NOT NULL CHECK(status IN ('pending','paid','shipped','delivered','completed','cancelled','refunded')),
 node_id text NOT NULL, owner_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), paid_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,order_no),
 FOREIGN KEY(tenant_id,channel_id) REFERENCES channels(tenant_id,id),
 FOREIGN KEY(tenant_id,sku_id) REFERENCES skus(tenant_id,id),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id),
 FOREIGN KEY(tenant_id,owner_id) REFERENCES users(tenant_id,id))""")
    op.execute("CREATE INDEX idx_orders_created ON orders(tenant_id,created_at)")
    op.execute("CREATE INDEX idx_orders_channel ON orders(tenant_id,channel_id,created_at)")
    op.execute("""CREATE TABLE after_sales(
 id text PRIMARY KEY, tenant_id text NOT NULL, order_id text NOT NULL,
 type text NOT NULL CHECK(type IN ('refund','return','exchange')),
 reason text NOT NULL, amount_minor bigint NOT NULL CHECK(amount_minor>=0),
 status text NOT NULL CHECK(status IN ('pending','approved','rejected','resolved')),
 created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,order_id) REFERENCES orders(tenant_id,id))""")
    op.execute("CREATE INDEX idx_as_created ON after_sales(tenant_id,created_at)")
    op.execute("""CREATE TABLE stock(
 tenant_id text NOT NULL, sku_id text NOT NULL, warehouse text NOT NULL,
 qty integer NOT NULL CHECK(qty>=0), safety_qty integer NOT NULL DEFAULT 0,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,sku_id,warehouse),
 FOREIGN KEY(tenant_id,sku_id) REFERENCES skus(tenant_id,id))""")
    for t, note in (("products","合成学习数据：SPU 商品主数据"),("skus","合成学习数据：SKU 规格"),
                    ("channels","合成学习数据：销售渠道"),("orders","合成学习数据：订单流水"),
                    ("after_sales","合成学习数据：售后工单"),("stock","合成学习数据：库存快照")):
        op.execute(f"COMMENT ON TABLE {t} IS '{note}（scripts/seed_business_data.py 确定性生成，可复现）'")

def downgrade():
    for t in ("stock","after_sales","orders","channels","skus","products"):
        op.execute(f"DROP TABLE {t}")
