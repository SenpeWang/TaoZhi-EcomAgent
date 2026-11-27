"""语义向量列迁移：chunks.embedding 的加入与回退。"""

from alembic import op
revision="004_semantic_vector"
down_revision="003_source_facts"
branch_labels=None
depends_on=None
def upgrade():
    """为 chunks 表增加 BYTEA 类型的 embedding 列以暂存向量字节。"""
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding BYTEA")
def downgrade():
    """删除 chunks 表的 embedding 列。"""
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
