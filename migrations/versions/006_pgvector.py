"""chunks.embedding 迁移到 pgvector vector(512) 并建 HNSW 索引。

背景：向量检索从 Elasticsearch dense_vector 迁回 PostgreSQL（pgvector），ES 只保留中文 BM25。
注意：CREATE EXTENSION vector 需超级用户（本库已由 sp 超管预建），应用用户无权限时迁移仅提示不失败。
"""
from alembic import op
import numpy as np

revision="006_pgvector"
down_revision="005_task_outbox"
branch_labels=None
depends_on=None

DIM=512

def upgrade():
    """启用 pgvector 扩展，把 chunks.embedding 迁移为 vector(512) 并建立 HNSW 索引。"""
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
        CREATE EXTENSION vector;
      END IF;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE 'vector 扩展需由超级用户预先创建（CREATE EXTENSION vector;）';
    END $$;""")
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_v vector(512)")
    conn=op.get_bind()
    rows=conn.exec_driver_sql("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL AND embedding_v IS NULL").fetchall()
    for i in range(0,len(rows),1000):
        for cid,raw in rows[i:i+1000]:
            if not raw or len(raw)!=DIM*4:continue
            vec="["+",".join(repr(float(x)) for x in np.frombuffer(raw,dtype="float32").tolist())+"]"
            conn.exec_driver_sql("UPDATE chunks SET embedding_v=%s::vector WHERE id=%s",(vec,cid))
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE chunks RENAME COLUMN embedding_v TO embedding")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=100)")

def downgrade():
    """删除 HNSW 索引，并把 embedding 退回 BYTEA 存储。"""
    op.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw_idx")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding BYTEA")
