"""为管理员问答、调研轨迹的跨用户游标分页建立部分索引。"""
from alembic import op

revision = "013_trace_pagination"
down_revision = "012_system_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """在线构建索引，允许任务在迁移过程中继续入队。"""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_trace_page")
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_jobs_trace_page "
            "ON jobs(tenant_id, created_at DESC, id DESC) WHERE kind IN ('ask', 'research')"
        )


def downgrade() -> None:
    """在线移除轨迹分页索引。"""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_jobs_trace_page")
