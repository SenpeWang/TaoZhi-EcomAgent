"""登记评测数据集并把批次级指标落成可查询列。"""
from alembic import op

revision = "021_eval_dataset_metrics"
down_revision = "020_evaluation_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增评测数据集登记表，并为批次补充通过率、时延、token 与判定明细列。"""
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_datasets (
         dataset_id text PRIMARY KEY,
         name text NOT NULL,
         source text NOT NULL CHECK (source IN ('live','synthetic_trace_regrade')),
         case_count integer NOT NULL CHECK (case_count >= 0),
         schema_version integer NOT NULL CHECK (schema_version > 0),
         first_seen_at timestamptz NOT NULL DEFAULT now(),
         updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        ALTER TABLE eval_runs
         ADD COLUMN IF NOT EXISTS dataset_id text REFERENCES eval_datasets(dataset_id),
         ADD COLUMN IF NOT EXISTS pass_rate double precision,
         ADD COLUMN IF NOT EXISTS mean_score double precision,
         ADD COLUMN IF NOT EXISTS latency_p50_ms integer,
         ADD COLUMN IF NOT EXISTS latency_p95_ms integer,
         ADD COLUMN IF NOT EXISTS total_tokens integer,
         ADD COLUMN IF NOT EXISTS judges jsonb
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_runs_dataset
        ON eval_runs(tenant_id,dataset_id,created_at DESC,run_id DESC)
    """)


def downgrade() -> None:
    """删除批次新增指标列、数据集索引与 eval_datasets 表。"""
    op.execute("DROP INDEX IF EXISTS idx_eval_runs_dataset")
    op.execute("""
        ALTER TABLE eval_runs
         DROP COLUMN IF EXISTS judges,
         DROP COLUMN IF EXISTS total_tokens,
         DROP COLUMN IF EXISTS latency_p95_ms,
         DROP COLUMN IF EXISTS latency_p50_ms,
         DROP COLUMN IF EXISTS mean_score,
         DROP COLUMN IF EXISTS pass_rate,
         DROP COLUMN IF EXISTS dataset_id
    """)
    op.drop_table("eval_datasets")
