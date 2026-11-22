# 测试

企业边界与回归测试，使用 pytest 组织，统一通过 `make test` 执行；远端 CI 调用同一组 Makefile 目标。

- 测试环境只有一份定义：仓库内 `config/test.env`，`ECOM_MODE=test` 时由配置层自动解析，`make test` 会带上该模式；
- 测试库固定为 `ecom_test`，连接串不含口令；口令由宿主的 `PGPASSWORD` 注入，本地取 `.env`，远端 CI 用服务占位符；
- 首次使用或结构变更后先执行 `make migrate-test` 迁移测试库；集成测试会清空该库的租户与业务数据；
- 用例自行构造事实，不读取 `data/` 下的企业资料；
- `slow` 标记用例默认排除，由 `-m 'not slow'` 控制；
- 分层与仓库守卫分别由 `test_layering.py`、`test_repository_guard.py` 约束；
- `test_test_environment.py` 约束测试环境免密、解析路径正确，以及远端工作流不得自行声明测试变量。
