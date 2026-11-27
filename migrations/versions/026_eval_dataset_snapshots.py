"""评测数据集内容快照与批次运行序号。

为每次评测运行固化不可变的数据集内容（全部套件用例与文件摘要），并在
``eval_runs`` 上记录快照关联、运行序号与重跑来源，支持同一数据集多次运行的区分。
"""
from alembic import op

revision = "026_eval_dataset_snapshots"
down_revision = "025_eval_migrate_versions_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增数据集内容快照表与快照用例表，并为批次记录快照关联、运行序号与重跑来源。"""
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_dataset_snapshots (
            snapshot_id text PRIMARY KEY,
            tenant_id text NOT NULL,
            eval_kind text NOT NULL,
            dataset_version text NOT NULL,
            primary_dataset_id text,
            bundle_sha256 text NOT NULL,
            case_count integer NOT NULL DEFAULT 0,
            files jsonb NOT NULL DEFAULT '{}'::jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            created_by text,
            CONSTRAINT uq_eval_dataset_snapshot
                UNIQUE (tenant_id, eval_kind, dataset_version, bundle_sha256)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_dataset_snapshot_cases (
            snapshot_id text NOT NULL
                REFERENCES eval_dataset_snapshots(snapshot_id) ON DELETE RESTRICT,
            suite text NOT NULL,
            case_id text NOT NULL,
            case_sha256 text NOT NULL,
            ordinal integer NOT NULL DEFAULT 0,
            case_payload jsonb NOT NULL,
            PRIMARY KEY (snapshot_id, suite, case_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_eval_snapshot_cases_case "
               "ON eval_dataset_snapshot_cases (case_id)")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS dataset_snapshot_id text "
               "REFERENCES eval_dataset_snapshots(snapshot_id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS run_seq integer")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS parent_run_id text")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS trigger_note text")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_eval_runs_run_seq
        ON eval_runs (tenant_id, dataset_id, eval_kind, scope, mode, run_seq)
        WHERE run_seq IS NOT NULL
    """)


def downgrade() -> None:
    """删除批次的快照列、运行序号索引与两张快照表。"""
    op.execute("DROP INDEX IF EXISTS uq_eval_runs_run_seq")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS trigger_note")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS parent_run_id")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS run_seq")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS dataset_snapshot_id")
    op.execute("DROP TABLE IF EXISTS eval_dataset_snapshot_cases")
    op.execute("DROP TABLE IF EXISTS eval_dataset_snapshots")
