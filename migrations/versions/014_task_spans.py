"""持久化问答和调研的 OpenTelemetry 父子节点、状态和诊断属性。"""
from alembic import op

revision = "014_task_spans"
down_revision = "013_trace_pagination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建立 Span 表以及按任务执行版本分页读取节点的索引。"""
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_spans (
            id bigserial PRIMARY KEY,
            tenant_id text NOT NULL,
            job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            run_version bigint NOT NULL,
            trace_id varchar(32) NOT NULL,
            span_id varchar(16) NOT NULL,
            parent_span_id varchar(16),
            name text NOT NULL,
            kind text NOT NULL,
            status text NOT NULL CHECK (status IN ('running', 'ok', 'error')),
            started_at timestamptz NOT NULL,
            ended_at timestamptz,
            duration_ms bigint CHECK (duration_ms >= 0),
            error_type text,
            attributes jsonb NOT NULL DEFAULT '{}',
            UNIQUE (trace_id, span_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_spans_run ON task_spans(tenant_id,job_id,run_version,id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_spans_job ON task_spans(job_id)")


def downgrade() -> None:
    """移除任务 Span 表。"""
    op.execute("DROP TABLE task_spans")
