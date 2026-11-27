"""评测中心 V3：数据集版本、算法版本、基线与报告原文。

- eval_datasets 增加 suite/content_sha256/metadata/archived，承载版本化数据集
- eval_profiles 增加 version_no/digest/git_sha/dataset_version，承载算法版本
- eval_runs 增加 scope/dataset_version，区分评测口径与数据集版本快照
- 新增 eval_run_reports 存原始报告对象元数据，eval_baselines 存基线（active + 历史）
- jobs.kind 增加 evaluation，由 Worker 执行真实评测
"""
from alembic import op

revision = "023_eval_center_v3"
down_revision = "022_eval_profiles_and_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为数据集、批次与配置版本增加版本化字段，新增报告原文表、基线表与评测任务类型。"""
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_kind_check")
    op.execute("""
        ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
        CHECK (kind IN ('ask','ingest','research','evaluation'))
    """)
    op.execute("""
        ALTER TABLE eval_datasets
         ADD COLUMN IF NOT EXISTS suite text NOT NULL DEFAULT '',
         ADD COLUMN IF NOT EXISTS content_sha256 text NOT NULL DEFAULT '',
         ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
         ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false
    """)
    op.execute("""
        ALTER TABLE eval_runs
         ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'full',
         ADD COLUMN IF NOT EXISTS dataset_version text NOT NULL DEFAULT ''
    """)
    op.execute("ALTER TABLE eval_runs DROP CONSTRAINT IF EXISTS eval_runs_scope_check")
    op.execute("""
        ALTER TABLE eval_runs ADD CONSTRAINT eval_runs_scope_check
        CHECK (scope IN ('full','quick','retrieval','adversarial','behavior'))
    """)
    op.execute("""
        ALTER TABLE eval_profiles
         ADD COLUMN IF NOT EXISTS version_no integer NOT NULL DEFAULT 0,
         ADD COLUMN IF NOT EXISTS digest text NOT NULL DEFAULT '',
         ADD COLUMN IF NOT EXISTS git_sha text,
         ADD COLUMN IF NOT EXISTS dataset_version text NOT NULL DEFAULT ''
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_profiles_digest
        ON eval_profiles(tenant_id, eval_kind, digest) WHERE digest <> ''
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_profiles_version_no
        ON eval_profiles(tenant_id, eval_kind, version_no) WHERE version_no > 0
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_run_reports (
         tenant_id text NOT NULL,
         run_id text NOT NULL,
         object_key text NOT NULL,
         sha256 text NOT NULL,
         byte_size integer NOT NULL CHECK (byte_size >= 0),
         content_type text NOT NULL DEFAULT 'application/json',
         created_at timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (tenant_id, run_id),
         FOREIGN KEY (tenant_id, run_id) REFERENCES eval_runs(tenant_id, run_id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_baselines (
         baseline_id text PRIMARY KEY,
         tenant_id text NOT NULL,
         eval_kind text NOT NULL CHECK (eval_kind IN ('agent','rag')),
         dataset_id text NOT NULL REFERENCES eval_datasets(dataset_id),
         dataset_version text NOT NULL,
         scope text NOT NULL CHECK (scope IN ('full','quick','retrieval','adversarial','behavior')),
         mode text NOT NULL CHECK (mode IN ('live','synthetic_trace_regrade')),
         run_id text NOT NULL,
         note text NOT NULL DEFAULT '',
         created_by text NOT NULL,
         created_at timestamptz NOT NULL DEFAULT now(),
         active boolean NOT NULL DEFAULT true,
         superseded_at timestamptz,
         FOREIGN KEY (tenant_id, run_id) REFERENCES eval_runs(tenant_id, run_id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_baselines_active
        ON eval_baselines(tenant_id, eval_kind, dataset_version, scope, mode) WHERE active
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_baselines_dataset
        ON eval_baselines(tenant_id, eval_kind, dataset_version, created_at DESC)
    """)
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run','set_baseline'))
    """)


def downgrade() -> None:
    """删除基线表、报告表、版本化索引与列，并还原 jobs 的任务类型约束。"""
    op.execute("DROP TABLE IF EXISTS eval_baselines")
    op.execute("DROP TABLE IF EXISTS eval_run_reports")
    op.execute("DROP INDEX IF EXISTS uq_eval_profiles_version_no")
    op.execute("DROP INDEX IF EXISTS uq_eval_profiles_digest")
    op.execute("ALTER TABLE eval_profiles DROP COLUMN IF EXISTS dataset_version")
    op.execute("ALTER TABLE eval_profiles DROP COLUMN IF EXISTS git_sha")
    op.execute("ALTER TABLE eval_profiles DROP COLUMN IF EXISTS digest")
    op.execute("ALTER TABLE eval_profiles DROP COLUMN IF EXISTS version_no")
    op.execute("ALTER TABLE eval_runs DROP CONSTRAINT IF EXISTS eval_runs_scope_check")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS dataset_version")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS scope")
    op.execute("ALTER TABLE eval_datasets DROP COLUMN IF EXISTS archived")
    op.execute("ALTER TABLE eval_datasets DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE eval_datasets DROP COLUMN IF EXISTS content_sha256")
    op.execute("ALTER TABLE eval_datasets DROP COLUMN IF EXISTS suite")
    op.execute("ALTER TABLE eval_audit_logs DROP CONSTRAINT IF EXISTS eval_audit_logs_action_check")
    op.execute("""
        ALTER TABLE eval_audit_logs ADD CONSTRAINT eval_audit_logs_action_check
        CHECK (action IN ('register_dataset','register_profile','trigger_run','publish_run'))
    """)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_kind_check")
    op.execute("""
        ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
        CHECK (kind IN ('ask','ingest','research'))
    """)
