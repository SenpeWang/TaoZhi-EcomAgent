# 项目结构与目录契约

本文件登记公开目录及职责。新增、删除或移动目录必须在同一提交修改目录表、README 结构和所有相关引用；提交检查拒绝未登记目录。

## 公开目录登记

| 目录 | 职责 |
| --- | --- |
| `.github/` | GitHub 检查及中文问题反馈，不使用 PR 模板 |
| `.github/ISSUE_TEMPLATE/` | 中文 Bug、功能、文档与使用问题表单 |
| `.github/workflows/` | main 推送触发的最小权限 CI，不自动部署 |
| `.githooks/` | 每份克隆主动启用的提交与推送门禁 |
| `k8s/` | 生产 Kubernetes 容器编排与 HPA 弹性伸缩清单 |
| `migrations/` | Alembic 环境 |
| `migrations/versions/` | 只追加已验证的数据库升级版本 |
| `scripts/` | 部署、数据管理、构建、验收与提交检查 |
| `src/` | Python 源码根目录 |
| `src/ecom_copilot/` | 共享能力与在线入口，保持统一授权路径 |
| `src/ecom_copilot/agents/` | 已有离线 Agent 编排与共享组件 |
| `src/ecom_copilot/api/` | 兼容入口与隔离的旧接口测试 |
| `src/ecom_copilot/api/routes/` | 旧离线路由，不作为在线授权旁路 |
| `src/ecom_copilot/business/` | 只读业务抽象与离线回归 |
| `src/ecom_copilot/context/` | 上下文构造 |
| `src/ecom_copilot/debate/` | 离线证据讨论 |
| `src/ecom_copilot/enterprise/` | 在线权限、会话、文档、业务服务、工作流及 Worker |
| `src/ecom_copilot/graph/` | 本地图谱与共享图能力 |
| `evals/` | 固定评测基准与基线分数 |
| `src/ecom_copilot/hypothesis/` | 离线假设管理 |
| `src/ecom_copilot/ingestion/` | 资料来源与入库共享工具 |
| `src/ecom_copilot/llm/` | 模型客户端、路由与直连控制 |
| `src/ecom_copilot/memory/` | 旧离线记忆；在线来源权限由 enterprise 管理 |
| `src/ecom_copilot/observability/` | 脱敏追踪与可观测性 |
| `src/ecom_copilot/parsing/` | 文档格式解析 |
| `src/ecom_copilot/retrieval/` | 共享本地检索与排序 |
| `src/ecom_copilot/safety/` | 输入与证据安全 |
| `src/ecom_copilot/schemas/` | 共享结构和类型 |
| `src/ecom_copilot/security/` | 旧鉴权隔离回归，不作为在线入口 |
| `src/ecom_copilot/storage/` | 离线持久化，不用于在线权限和队列 |
| `tests/` | 单元与企业边界验收，测试事实自行构造 |
| `web/` | 前端依赖、锁与构建配置 |
| `web/src/` | Vue 页面、类型、样式与 API 调用 |

## 根文件

README.md 为 GitHub 项目介绍。Makefile 是统一维护命令，Dockerfile 为容器化运行时定义，alembic.ini 配置迁移，requirements.txt 声明依赖，requirements.lock.txt 锁定验证版本，.env.example 仅无秘密占位，.gitignore 阻止私有和生成物。.editorconfig 统一编辑器换行和缩进；web/eslint.config.js、.prettierrc.json、commitlint.config.cjs 分别定义代码、格式和消息检查，scripts/check_development.py 调度实际快照门禁。

AGENTS.md 是本机维护约束，项目说明.md 是本机个人学习文档；两者都不跟踪或提交，公开文档不链接或依赖它们。维护者在本机维护这些文件，克隆仓库无需取得它们。

## 私有数据（整个 data 忽略）

| 用途 | 本机位置 |
| --- | --- |
| 待授权入库资料 | data/incoming/<tenant_id>/ |
| 本机演示语料 | data/corpus/<category>/ |
| 已入库私有原件 | data/private/<mode>/ |
| 日志、验证截图与任务证据 | data/runtime/ |
| 授权业务导出 | data/reports/<tenant_id>/ |
| 检索、向量及图谱 | data/index/、data/embeddings/、data/graph/ |
| 旧业务数据库迁移来源 | data/archive/ |
| 明确请求的备份 | data/backups/ |

无需提前创建空目录。企业文档和生成物统一进入 data/；放入目录不自动授予阅读、发布、下载或模型处理权限。不得把资料移到源码、测试、软件说明或静态公开目录来逃避忽略规则。

虚拟环境、node_modules、web/dist、私有 .env* 和主机用户运行时也不提交。业务数据库不使用 Git 保存。

## 依赖与同步

页面调用统一 API，API 加载当前会话并使用业务服务；权限集中 enterprise/policy.py。Worker 和 Agent 使用同一权限与来源门禁。PostgreSQL 保存在线状态、任务队列和 outbox；附件原件存 MinIO 桶 ecom-private（enterprise/storage.py，可回落本地私有目录）；任务事件经 Kafka ecom.tasks 主题分发（enterprise/queue.py，outbox 发布 + 幂等认领，轮询兜底）；检索切片同步 ES 索引 chunks_v1（enterprise/es_index.py，BM25+kNN 双路召回并前置权限过滤，可回落本地五信号链路）。

原模块仍用于共享或离线回归；删除前检查全部引用。新在线能力不得另起旧 JWT、SQLite 或平行授权路径。

新增目录先明确职责和数据属性，再归位并登记。接口、角色、字段、配置、迁移、命名或脚本变更同步前后端、测试、启动和软件说明；完整对应表见 CONTRIBUTING.md。代码、契约、文档和验证一致才算完成。
