"""初始企业版表结构迁移：租户、账号、组织与基础业务实体。"""

from pathlib import Path
from alembic import op
revision="001_enterprise"
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    """读取并逐条执行企业库基础结构 SQL，建立初始表结构。"""
    source=Path(__file__).resolve().parents[2]/"src/taozhi/server/schema.sql"
    for statement in source.read_text().split(";"):
        if statement.strip():op.get_bind().exec_driver_sql(statement)
def downgrade():
    """拒绝自动回退，要求恢复经验证的备份以保护企业数据。"""
    raise RuntimeError("回退请恢复经验证的备份，禁止自动删除企业数据")
