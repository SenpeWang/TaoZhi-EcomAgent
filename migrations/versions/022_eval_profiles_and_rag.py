"""新增不可变技术栈/Prompt 配置版本表、RAG/Agent 双体系字段与窄写审计日志。"""
from alembic import op

revision = "022_eval_profiles_and_rag"
down_revision = "021_eval_dataset_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为数据集与批次补充 agent/rag 体系字段，新增不可变配置版本表与审计日志表。"""
    op.execute("""
        ALTER TABLE eval_datasets
         ADD COLUMN IF NOT EXISTS eval_kind text NOT NULL DEFAULT 'agent' CHECK (eval_kind IN ('agent', 'rag')),
         ADD COLUMN IF NOT EXISTS version text NOT NULL DEFAULT 'v1',
         ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
         ADD COLUMN IF NOT EXISTS cases_payload jsonb NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_profiles (
         profile_id text PRIMARY KEY,
         tenant_id text NOT NULL REFERENCES tenants(id),
         eval_kind text NOT NULL CHECK (eval_kind IN ('agent', 'rag')),
         version_tag text NOT NULL,
         tech_stack jsonb NOT NULL DEFAULT '{}'::jsonb,
         retrieval_params jsonb NOT NULL DEFAULT '{}'::jsonb,
         prompt_snapshots jsonb NOT NULL DEFAULT '{}'::jsonb,
         prompt_sha256 text NOT NULL,
         notes text NOT NULL DEFAULT '',
         created_by text NOT NULL,
         created_at timestamptz NOT NULL DEFAULT now(),
         UNIQUE (tenant_id, eval_kind, version_tag)
        )
    """)
    op.execute("""
        ALTER TABLE eval_runs
         ADD COLUMN IF NOT EXISTS eval_kind text NOT NULL DEFAULT 'agent' CHECK (eval_kind IN ('agent', 'rag')),
         ADD COLUMN IF NOT EXISTS profile_id text REFERENCES eval_profiles(profile_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_runs_kind_profile
        ON eval_runs(tenant_id, eval_kind, profile_id, created_at DESC, run_id DESC)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_audit_logs (
         audit_id text PRIMARY KEY,
         tenant_id text NOT NULL,
         actor_id text NOT NULL,
         action text NOT NULL CHECK (action IN ('register_dataset', 'register_profile', 'trigger_run', 'publish_run')),
         target_id text NOT NULL,
         git_sha text,
         summary jsonb NOT NULL DEFAULT '{}'::jsonb,
         created_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    """删除审计日志、配置版本表及新增的体系字段。"""
    op.execute("DROP TABLE IF EXISTS eval_audit_logs")
    op.execute("DROP INDEX IF EXISTS idx_eval_runs_kind_profile")
    op.execute("""
        ALTER TABLE eval_runs
         DROP COLUMN IF EXISTS profile_id,
         DROP COLUMN IF EXISTS eval_kind
    """)
    op.execute("DROP TABLE IF EXISTS eval_profiles")
    op.execute("""
        ALTER TABLE eval_datasets
         DROP COLUMN IF EXISTS cases_payload,
         DROP COLUMN IF EXISTS description,
         DROP COLUMN IF EXISTS version,
         DROP COLUMN IF EXISTS eval_kind
    """)
