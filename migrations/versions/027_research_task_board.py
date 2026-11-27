"""调研任务板：持久子任务、依赖、评论与 Worker 结果。

任务板是调研多智能体运行的持久事实源；LangGraph state 只保留执行态。
"""
from alembic import op

revision = "027_research_task_board"
down_revision = "026_eval_dataset_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建调研任务板的任务、依赖与评论三张表及其查询索引。"""
    op.execute("""
        CREATE TABLE IF NOT EXISTS research_tasks (
            id text PRIMARY KEY,
            tenant_id text NOT NULL,
            job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            run_version bigint NOT NULL,
            parent_task_id text REFERENCES research_tasks(id) ON DELETE SET NULL,
            task_key text NOT NULL,
            role text NOT NULL,
            status text NOT NULL DEFAULT 'ready',
            revision integer NOT NULL DEFAULT 1,
            owner_agent text,
            brief jsonb NOT NULL DEFAULT '{}'::jsonb,
            result jsonb,
            error text NOT NULL DEFAULT '',
            write_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz,
            CONSTRAINT research_tasks_status_check CHECK (
                status IN ('ready','running','done','failed','blocked','cancelled')
            ),
            CONSTRAINT research_tasks_unique_key UNIQUE (job_id, run_version, task_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_research_tasks_job "
               "ON research_tasks (tenant_id, job_id, run_version)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_research_tasks_status "
               "ON research_tasks (status)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS research_task_deps (
            task_id text NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
            depends_on_id text NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
            PRIMARY KEY (task_id, depends_on_id),
            CHECK (task_id <> depends_on_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS research_task_comments (
            id bigserial PRIMARY KEY,
            task_id text NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
            author_agent text NOT NULL,
            body text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_research_task_comments_task "
               "ON research_task_comments (task_id, created_at)")


def downgrade() -> None:
    """按依赖倒序删除任务评论、依赖与任务表。"""
    op.execute("DROP TABLE IF EXISTS research_task_comments")
    op.execute("DROP TABLE IF EXISTS research_task_deps")
    op.execute("DROP TABLE IF EXISTS research_tasks")
