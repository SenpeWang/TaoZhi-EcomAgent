# 测试

企业边界与回归测试，使用 pytest 组织，统一通过 `make test` 执行。

- 运行前确保 `.env.test` 指向隔离的 `ecom_test` 库；
- 用例自行构造事实，不读取 `data/` 下的企业资料；
- `slow` 标记用例默认排除，由 `-m 'not slow'` 控制；
- 分层与仓库守卫分别在 `test_layering.py`、`test_repository_guard.py` 中约束。
