"""Worker 心跳表迁移。"""

from alembic import op
revision="002_worker_health"
down_revision="001_enterprise"
branch_labels=None
depends_on=None
def upgrade():
    """创建 worker_heartbeats 表记录工作者心跳。"""
    op.execute("CREATE TABLE worker_heartbeats(id text PRIMARY KEY,updated_at timestamptz NOT NULL DEFAULT now())")
def downgrade():
    """删除 worker_heartbeats 心跳表。"""
    op.execute("DROP TABLE worker_heartbeats")
