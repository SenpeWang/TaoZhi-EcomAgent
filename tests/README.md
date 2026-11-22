# 测试

企业边界与回归测试，使用 pytest 组织，统一通过 `make test` 执行。

- 测试环境由 `config/test.env` 统一提供，测试库固定为 `ecom_test`；
- 用例自行构造事实，不读取 `data/` 下的企业资料；
- `slow` 标记用例默认排除，由 `-m 'not slow'` 控制；
- 分层与仓库守卫分别在 `test_layering.py`、`test_repository_guard.py` 中约束。
