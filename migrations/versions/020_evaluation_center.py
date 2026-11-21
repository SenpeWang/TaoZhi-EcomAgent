"""为评测批次与用例明细建立租户约束和分页索引。"""
from alembic import op

revision = "020_evaluation_center"
down_revision = "019_bi_result_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
         tenant_id text NOT NULL, run_id text NOT NULL, owner_id text NOT NULL, created_at timestamptz NOT NULL,
         mode text NOT NULL CHECK (mode IN ('live','synthetic_trace_regrade')), git_sha text,
         dataset_sha256 text NOT NULL, judges_sha256 text NOT NULL, report_sha256 text NOT NULL, baseline_sha256 text,
         metrics jsonb NOT NULL, gate_passed boolean NOT NULL, passed_cases integer NOT NULL CHECK (passed_cases >= 0),
         failed_cases integer NOT NULL CHECK (failed_cases >= 0), unassessable_cases integer NOT NULL CHECK (unassessable_cases >= 0),
         PRIMARY KEY (tenant_id,run_id), UNIQUE (tenant_id,report_sha256),
         FOREIGN KEY (tenant_id,owner_id) REFERENCES users(tenant_id,id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_runs_date ON eval_runs(tenant_id,created_at DESC,run_id DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_runs_owner ON eval_runs(tenant_id,owner_id,created_at DESC,run_id DESC)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_case_results (
         tenant_id text NOT NULL, run_id text NOT NULL, case_id text NOT NULL, case_sha256 text NOT NULL,
         name text NOT NULL, category text, suite text NOT NULL, status text NOT NULL CHECK(status IN ('passed','failed','unassessable')),
         score double precision CHECK (score BETWEEN 0 AND 1), hard_failure boolean NOT NULL, trace_id varchar(32),
         expected jsonb NOT NULL, observed jsonb, steps_complete boolean, verdicts jsonb NOT NULL,
         PRIMARY KEY (tenant_id,run_id,case_id),
         FOREIGN KEY (tenant_id,run_id) REFERENCES eval_runs(tenant_id,run_id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eval_case_results_status ON eval_case_results(tenant_id,run_id,status,case_id)
    """)


def downgrade() -> None:
    op.drop_table("eval_case_results")
    op.drop_table("eval_runs")
