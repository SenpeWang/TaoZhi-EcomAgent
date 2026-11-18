"""高频运行时表索引补齐迁移：task_events / model_calls / sessions 复合与单列索引。"""
from __future__ import annotations

from alembic import op

revision = "012_system_indexes"
down_revision = "011_job_fk_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """补充 SSE 增量游标、模型预算计数与过期会话清理所需的生产级索引。"""
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_events_job_cursor "
        "ON task_events(tenant_id, job_id, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_calls_job "
        "ON model_calls(job_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_expires "
        "ON sessions(expires_at)"
    )


def downgrade() -> None:
    """移除高频运行时表新增索引。"""
    op.execute("DROP INDEX IF EXISTS idx_sessions_expires")
    op.execute("DROP INDEX IF EXISTS idx_model_calls_job")
    op.execute("DROP INDEX IF EXISTS idx_task_events_job_cursor")
