"""在现有任务事件序列中存放可续传的正文增量。"""
from alembic import op

revision = "017_task_output_events"
down_revision = "016_trace_activity_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """历史记录保持阶段事件，新正文与阶段使用同一递增游标。"""
    op.execute("""ALTER TABLE task_events
        ADD COLUMN IF NOT EXISTS event_type text NOT NULL DEFAULT 'stage',
        ADD COLUMN IF NOT EXISTS output_text text""")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='task_events'::regclass
                       AND conname='task_events_output_check') THEN
            ALTER TABLE task_events ADD CONSTRAINT task_events_output_check CHECK (
                (event_type IN ('stage','result_reset') AND output_text IS NULL)
                OR (event_type='result_delta' AND output_text IS NOT NULL AND output_text<>'')
            );
        END IF;
    END $$""")


def downgrade() -> None:
    """移除正文事件并恢复纯阶段日志。"""
    op.execute("DELETE FROM task_events WHERE event_type<>'stage'")
    op.execute("ALTER TABLE task_events DROP COLUMN output_text, DROP COLUMN event_type")
