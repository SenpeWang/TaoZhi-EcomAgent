"""为指定 Trace ID 的活动时间顺序与稳定游标建立索引。"""
from alembic import op

revision = "016_trace_activity_time"
down_revision = "015_span_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """在线建立时间顺序索引，允许迁移期间继续采集任务节点。"""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_task_spans_activity_time")
        op.execute("CREATE INDEX CONCURRENTLY idx_task_spans_activity_time ON task_spans(trace_id,started_at,id)")


def downgrade() -> None:
    """移除活动读取专用索引。"""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_task_spans_activity_time")
