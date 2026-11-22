# 数据库迁移

使用 Alembic 管理表结构版本，`versions/` 内的文件只追加，不修改已发布的迁移。

- 生成新版本：`alembic revision -m "说明"`
- 应用迁移：`make rebuild` 启动时由 `migrate` 容器执行 `alembic upgrade head`
- 测试库迁移：`make migrate-test`，以 `ECOM_MODE=test` 读取 `config/test.env`
- 校验现状：`docker compose exec api alembic current`
- 迁移必须可重复执行，涉及索引与约束时先在隔离库验证
