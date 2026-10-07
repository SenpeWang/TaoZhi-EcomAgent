from alembic import op
revision="003_source_facts"
down_revision="002_worker_health"
branch_labels=None
depends_on=None
def upgrade():
    op.execute("CREATE TABLE knowledge_mentions(id text PRIMARY KEY,tenant_id text NOT NULL REFERENCES tenants(id),document_id text NOT NULL REFERENCES documents(id),version integer NOT NULL,chunk_id text NOT NULL REFERENCES chunks(id),entity text NOT NULL)")
    op.execute("CREATE INDEX idx_mentions_doc ON knowledge_mentions(tenant_id,document_id,version)")
def downgrade():
    op.execute("DROP TABLE knowledge_mentions")
