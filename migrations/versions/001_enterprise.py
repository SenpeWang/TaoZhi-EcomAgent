from pathlib import Path
from alembic import op
revision="001_enterprise"
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    source=Path(__file__).resolve().parents[2]/"src/ecom_copilot/enterprise/schema.sql"
    for statement in source.read_text().split(";"):
        if statement.strip():op.get_bind().exec_driver_sql(statement)
def downgrade():
    raise RuntimeError("回退请恢复经验证的备份，禁止自动删除企业数据")
