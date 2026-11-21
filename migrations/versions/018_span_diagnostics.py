"""按节点留存有时间戳的结构化异常与降级诊断。"""
from alembic import op

revision = "018_span_diagnostics"
down_revision = "017_task_output_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """历史节点保留空诊断列表。"""
    op.execute("ALTER TABLE task_spans ADD COLUMN IF NOT EXISTS diagnostics jsonb NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    """移除诊断列表。"""
    op.execute("ALTER TABLE task_spans DROP COLUMN diagnostics")
