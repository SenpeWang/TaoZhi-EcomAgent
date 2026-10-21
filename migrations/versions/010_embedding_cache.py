"""嵌入缓存：embedding_cache 表。

入库（worker）、回填（scripts/backfill_embeddings.py）、检索侧惰性补算
（retriever._semantic_matrix）三处共用它复用切片向量，避免正文没变却反复
调用嵌入模型。

键 = (tenant_id, model_tag, digest)：digest 是嵌入输入（文档标题 + 正文）
的 sha256，model_tag 来自嵌入服务 /health 的 model:dim。
换模型后老键自动失效——不需要额外的清理动作，也不会把老口径向量当缓存用。
"""
from alembic import op

revision = "010_embedding_cache"
down_revision = "009_conversation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS embedding_cache(
 tenant_id text NOT NULL, model_tag text NOT NULL, digest text NOT NULL,
 dim integer NOT NULL, embedding vector(512) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_used_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,model_tag,digest))""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS embedding_cache")
