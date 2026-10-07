from alembic import op
revision="004_semantic_vector"
down_revision="003_source_facts"
branch_labels=None
depends_on=None
def upgrade():
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding BYTEA")
def downgrade():
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
