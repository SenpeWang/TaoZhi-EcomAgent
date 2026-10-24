"""多轮会话：jobs 增加 conversation_id，同一会话内继承上文证据。

说明：此前追问上下文靠问题文本里的"刚才/这款"等关键词猜，跨会话会串证据；
加显式会话标识后，继承范围限定在同一 conversation_id 内，空值保持历史行为。
"""
from alembic import op
revision="009_conversation"
down_revision="008_business_tables"
branch_labels=None
depends_on=None
def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS conversation_id text NOT NULL DEFAULT ''")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_conversation ON jobs(tenant_id,owner_id,conversation_id,created_at DESC)")
def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_jobs_conversation")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS conversation_id")
