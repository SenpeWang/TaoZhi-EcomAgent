"""为历史问数结果补存指标身份，供读取和审核时复核权限。"""
from alembic import op

revision = "019_bi_result_sources"
down_revision = "018_span_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """原指标已删除或标识已重用的结果保留无效来源，后续访问按受限处理。"""
    op.execute("""
        ALTER TABLE jobs ADD CONSTRAINT jobs_bi_requires_metric_source CHECK (
            kind <> 'ask' OR jsonb_typeof(result->'data') IS DISTINCT FROM 'object'
            OR sources @> '[{"kind":"metric"}]'::jsonb
        ) NOT VALID
    """)
    op.execute("""
        UPDATE jobs j SET sources = j.sources || jsonb_build_array(jsonb_build_object(
            'kind', 'metric', 'tenant_id', j.tenant_id, 'metric_id', (
                SELECT m.id FROM metrics m
                WHERE m.tenant_id=j.tenant_id AND m.metric_key=(j.result #>> '{data,metric_key}')
                    AND m.created_at <= COALESCE(j.started_at, j.created_at)
            )
        ))
        WHERE j.kind='ask' AND jsonb_typeof(j.result->'data')='object'
            AND NOT (j.sources @> '[{"kind":"metric"}]'::jsonb)
    """)
    op.execute("ALTER TABLE jobs VALIDATE CONSTRAINT jobs_bi_requires_metric_source")


def downgrade() -> None:
    """保留已回填的来源，避免降级重新开放受限历史结果。"""
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_bi_requires_metric_source")
