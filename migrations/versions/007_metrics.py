"""指标语义层：metrics + metric_grants。

指标定义（口径 SQL 模板/维度/密级/归属节点）独立于文档库，不污染 RAG 语料；
授权复用文档三级密级 + 组织节点 + 例外授予（metric_grants）。
NL2SQL 执行器只做模板填充，SQL 永不由 LLM 生成（见 metrics.py）。
"""
from alembic import op

revision="007_metrics"
down_revision="006_pgvector"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE metrics(
 id text PRIMARY KEY, tenant_id text NOT NULL, metric_key text NOT NULL,
 name text NOT NULL, aliases text[] NOT NULL DEFAULT '{}', description text NOT NULL DEFAULT '',
 sql_template text NOT NULL, dimensions text[] NOT NULL DEFAULT '{}',
 base_table text NOT NULL, unit text NOT NULL DEFAULT '', format text NOT NULL DEFAULT 'number',
 level integer NOT NULL CHECK(level BETWEEN 1 AND 3),
 scope text NOT NULL CHECK(scope IN ('company','org','selected')),
 node_id text NOT NULL, owner_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('draft','published','disabled')) DEFAULT 'draft',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,metric_key),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id),
 FOREIGN KEY(tenant_id,owner_id) REFERENCES users(tenant_id,id))""")
    op.execute("CREATE INDEX idx_metrics_tenant ON metrics(tenant_id,state)")
    # 例外授予：与 document_grants 同构（allow/deny 可覆盖密级默认）
    op.execute("""CREATE TABLE metric_grants(
 tenant_id text NOT NULL, metric_id text NOT NULL, user_id text NOT NULL,
 effect text NOT NULL CHECK(effect IN ('allow','deny')), granted_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(metric_id,user_id),
 FOREIGN KEY(tenant_id,metric_id) REFERENCES metrics(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id))""")

def downgrade():
    op.execute("DROP TABLE metric_grants")
    op.execute("DROP TABLE metrics")
