"""任务子表外键级联删除迁移：task_events / task_reviews / model_calls 引用 jobs(id) 补齐 ON DELETE CASCADE。"""
from __future__ import annotations

from alembic import op

revision = "011_job_fk_cascade"
down_revision = "010_embedding_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """将任务关联子表外键升级为级联删除，杜绝删除会话触发 ForeignKeyViolation。"""
    op.execute(
        """
        DO $$
        DECLARE
            rec RECORD;
        BEGIN
            FOR rec IN
                SELECT conrelid::regclass::text AS tbl, conname
                FROM pg_constraint
                WHERE contype = 'f'
                  AND confrelid = 'jobs'::regclass
                  AND conrelid IN ('task_events'::regclass, 'task_reviews'::regclass, 'model_calls'::regclass)
            LOOP
                EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', rec.tbl, rec.conname);
            END LOOP;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT task_events_job_id_fkey "
        "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE task_reviews ADD CONSTRAINT task_reviews_job_id_fkey "
        "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE model_calls ADD CONSTRAINT model_calls_job_id_fkey "
        "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    """回退任务子表级联删除外键至默认限制行为。"""
    op.execute("ALTER TABLE task_events DROP CONSTRAINT IF EXISTS task_events_job_id_fkey")
    op.execute("ALTER TABLE task_reviews DROP CONSTRAINT IF EXISTS task_reviews_job_id_fkey")
    op.execute("ALTER TABLE model_calls DROP CONSTRAINT IF EXISTS model_calls_job_id_fkey")
    op.execute("ALTER TABLE task_events ADD CONSTRAINT task_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id)")
    op.execute("ALTER TABLE task_reviews ADD CONSTRAINT task_reviews_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id)")
    op.execute("ALTER TABLE model_calls ADD CONSTRAINT model_calls_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id)")
