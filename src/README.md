# 源码根目录

`src/taozhi/` 为淘智工程根包，按职责分层，低层不得反向依赖高层，由 `tests/test_layering.py` 校验。

| 目录 | 职责 |
| --- | --- |
| `domain/` | 领域内核：实体、权限规则、口令策略、文本清洗与角色层级 |
| `application/` | 用例服务：问答、调研、文档、经营看板与评测 |
| `infrastructure/` | 适配层：持久化仓储、向量库、MCP 服务与任务队列 |
| `core/` | 智能体运行时：会话组合根、工具集、沙箱与记忆 |
| `knowledge/` | 检索与存储适配：切片、混合检索、语义缓存与外部中间件 |
| `server/` | 接口层：FastAPI 装配、认证与 Worker 守护进程 |
| `schemas/` | 请求与响应模型 |
| `observability/` | 轨迹与诊断埋点 |
| `ai/` | 大模型客户端与用量统计 |
| `orchestration/` | 确定性控制器与决策引擎 |

入口为 `taozhi.server.api:app` 与 `taozhi.server.worker`。
