"""Alembic 迁移环境：绑定项目配置、元数据与离线/在线迁移入口。"""

from alembic import context
from sqlalchemy import create_engine,pool
from taozhi.config import load_config
engine=create_engine(load_config().dsn.replace("postgresql://","postgresql+psycopg://",1),poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection,target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()