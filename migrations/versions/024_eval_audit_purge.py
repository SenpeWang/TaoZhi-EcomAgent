"""评测中心清理审计动作：允许记录数据清理（purge_data）。"""
from alembic import op

revision = "024_eval_audit_purge"
down_revision = "023_eval_center_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run','set_baseline','purge_data'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run','set_baseline'))
    """)