"""任务 outbox 表与索引迁移。"""

from alembic import op
revision="005_task_outbox"
down_revision="004_semantic_vector"
branch_labels=None
depends_on=None
def upgrade():
    """创建任务发件箱表 task_outbox，用于可靠投递任务事件。"""
    op.execute("""CREATE TABLE task_outbox(
        id bigserial PRIMARY KEY,
        job_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX task_outbox_created_idx ON task_outbox(created_at)")
def downgrade():
    """删除 task_outbox 发件箱表。"""
    op.execute("DROP TABLE task_outbox")
