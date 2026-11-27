"""评测中心审计动作：允许记录历史数据集版本迁移（migrate_dataset_versions）。"""
from alembic import op

revision = "025_eval_migrate_versions_audit"
down_revision = "024_eval_audit_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """放宽审计动作约束，允许记录 migrate_dataset_versions 数据集版本迁移。"""
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run',
                          'set_baseline','purge_data','migrate_dataset_versions'))
    """)


def downgrade() -> None:
    """删除该动作的历史日志并还原审计动作约束。"""
    op.execute("DELETE FROM eval_audit_logs WHERE action='migrate_dataset_versions'")
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run',
                          'set_baseline','purge_data'))
    """)
