from alembic import op
revision="002_worker_health"
down_revision="001_enterprise"
branch_labels=None
depends_on=None
def upgrade():
    op.execute("CREATE TABLE worker_heartbeats(id text PRIMARY KEY,updated_at timestamptz NOT NULL DEFAULT now())")
def downgrade():
    op.execute("DROP TABLE worker_heartbeats")
