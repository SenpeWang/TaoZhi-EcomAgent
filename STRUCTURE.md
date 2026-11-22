# 项目结构与目录契约

本文件登记公开目录及职责。新增、删除或移动目录必须在同一提交修改目录表、README 结构和所有相关引用；提交检查拒绝未登记目录。

## 公开目录登记

| 目录 | 职责 |
| --- | --- |
| `.github/` | GitHub 检查及中文问题反馈，不使用 PR 模板 |
| `.github/ISSUE_TEMPLATE/` | 中文 Bug、功能、文档与使用问题表单 |
| `.github/workflows/` | main 推送触发的最小权限 CI，不自动部署 |
| `.githooks/` | 每份克隆主动启用的提交与推送门禁 |
| `migrations/` | Alembic 环境 |
| `migrations/versions/` | 只追加已验证的数据库升级版本 |
| `scripts/` | 部署、数据管理、构建与运维；不含评估逻辑 |
| `src/` | Python 源码根目录 |
| `src/taozhi/` | 淘智 (TaoZhi) 智能体工程核心根包 |
| `src/taozhi/domain/` | 领域层：核心实体、权限规则、口令策略、注入防线、排序切片与数学/引文校验（零外部框架） |
| `src/taozhi/infrastructure/` | 基础设施层：数据库、知识、MCP、Kafka 队列及实时业务网关 |
| `src/taozhi/infrastructure/persistence/` | PostgreSQL 连接、业务仓储、受控指标 SQL 和工作流检查点 |
| `src/taozhi/infrastructure/knowledge/` | 文档解析、附件存储、混合检索、索引与语义缓存 |
| `src/taozhi/infrastructure/mcp/` | 电商与情报类标准 MCP 协议服务端（tools/list & tools/call）与智能体客户端适配器 |
| `src/taozhi/observability/` | 在线任务的 OpenTelemetry 采集、中文诊断和 Span 批量投递 |
| `src/taozhi/ai/` | 大模型客户端、用量统计与结构化输出解析 |
| `src/taozhi/core/` | 核心引擎与底层基础设施 |
| `src/taozhi/core/agent/` | 智能体运行时核心：Piagent `AgentSession` 会话组合根、隔离沙箱（Scratch / Artifacts）与工作区通信协议 |
| `src/taozhi/core/tools/` | 原子工具集与统一结构化 ToolOutcome / 行边界截断工具 |
| `src/taozhi/core/memory/` | 租户物理隔离长效记忆库与上下文检索服务 |
| `src/taozhi/application/` | 应用层：业务用例服务、RAG 问答工作流与多智能体自主调研执行图 |
| `src/taozhi/application/bi/` | 指标意图识别、受控问数工作流、工作台概览与经营看板查询 |
| `src/taozhi/application/common/` | 应用层公共任务守卫、Token 用量聚合与 OpenTelemetry 节点埋点 |
| `src/taozhi/application/documents/` | 文档上传、版本、授权和审批用例，包含 Worker 的解析索引与送审 |
| `src/taozhi/application/rag/` | 知识问答工作流、受控问数分发、查询扩展与引文发布 |
| `src/taozhi/application/research/` | 调研任务执行、会话查询与中途干预，包含协调器、并行子智能体和研报合成 |
| `src/taozhi/application/services/` | 账号审批、任务生命周期、流式输出、工具调用、轨迹与评测查询 |
| `src/taozhi/orchestration/` | 编排辅助层：知识问答确定性控制器与离散决策引擎 |
| `src/taozhi/schemas/` | 接口 DTO 层：Pydantic 请求验证模型（认证、管理、文档、任务与工具） |
| `src/taozhi/server/` | 接口适配层：FastAPI 应用装配、Session/CSRF 认证与 Worker 租约守护进程 |
| `src/taozhi/server/routers/` | 接口路由层：系统监控、认证、组织审批、文档与任务 SSE 分领域 HTTP 路由 |
| `evals/` | 评估模块（独立单元）：口径说明、数据集、指标、运行器、基线与报告 |
| `evals/rag_eval/` | 企业知识库 RAG 三层评测运行器、质量门禁与实战测评记录（`测评记录.md`） |
| `evals/agent_eval/` | 自治多智能体能力、工具调用、可观测评测体系与实战测评记录（`测评记录.md`） |
| `evals/baseline/` | 基线只读归档：一次全量跑批一份快照（文件名=开跑时间），只增不改 |
| `evals/dataset/` | 独立评测数据包：规格、契约校验、确定性生成与幂等灌库 |
| `evals/dataset/spec/` | 数据包规格（组织/夹具/语料/模板），唯一事实来源 |
| `evals/datasets/` | 评测数据集与口径说明，随代码进 Git 可 diff |
| `evals/datasets/generated/` | 独立包生成产物：用例、语料与 manifest，随代码进 Git |
| `evals/datasets/generated/latest/` | 独立包生成产物目录，generate 直接写入（唯一一份，版本由 Git 承担） |
| `evals/reports/` | 运行产物不入库（.gitignore 全忽略） |
| `evals/tests/` | 评估代码自身的单元测试，必须进 CI |
| `tests/` | 单元与企业边界验收，测试事实自行构造 |
| `data/` | 公开配件 PDF、演示与评测资产，以及各环境的数据存放位置 |
| `web/` | 前端依赖、锁与构建配置 |
| `web/src/` | Vue 页面、类型、样式与 API 调用 |
| `web/src/assets/` | 静态图片与品牌图标资源 |
| `web/src/composables/` | 前端共享响应式组合式逻辑（如 `useTaskStream` 统一 SSE 任务进度订阅） |
| `web/src/views/` | 资料管理与权限表单、经营看板、智能体调研和轨迹分析等业务视图 |

## 根文件

README.md 为 GitHub 项目介绍。Makefile 是统一维护命令，Dockerfile 定义前端构建、应用运行时与语义向量容器多目标镜像，docker-compose.yml 与 docker-compose.gpu.yml 编排全官方中间件（pgvector PG 18、ES 9.2.3、Apache Kafka 4.1.2、MinIO）及应用服务，.dockerignore 隔离私有数据与构建缓存，alembic.ini 配置迁移，requirements.txt 声明依赖，requirements.lock.txt 锁定验证版本，.env.example 仅无秘密占位，.gitignore 阻止私有和生成物。.editorconfig 统一编辑器换行和缩进；web/eslint.config.js、.prettierrc.json、web/commitlint.config.cjs 分别定义代码、格式和消息检查，scripts/check_development.py 调度实际快照门禁。提交必须遵循 Conventional Commits 规范与树状多层级 Body，向 GitHub 远端推送必须严格遵守主动向 Senpe 询问确认的铁律纪律。

AGENTS.md 是本机维护约束，项目说明.md 是本机个人学习文档；两者都不跟踪或提交，公开文档不链接或依赖它们。维护者在本机维护这些文件，克隆仓库无需取得它们。

## 资料与运行数据

| 用途 | 本机位置 |
| --- | --- |
| 公开配件 PDF 与许可清单 | data/accessory_pdfs/ |
| 待授权入库资料 | data/incoming/<tenant_id>/ |
| 本机演示语料 | data/corpus/<category>/ |
| 已入库私有原件 | data/private/<mode>/ |
| 日志、验证截图与任务证据 | data/runtime/ |
| 授权业务导出 | data/reports/<tenant_id>/ |
| 检索、向量及图谱 | data/index/、data/embeddings/、data/graph/ |
| 旧业务数据库迁移来源 | data/archive/ |
| 明确请求的备份 | data/backups/ |

公开 PDF 随来源和许可清单提交。企业附件、业务数据库和各环境生成物由对应环境保管。文档的阅读、下载和外部模型处理由应用权限控制。[数据用途与导入](data/README.md)

## 依赖与同步

FastAPI 和 Worker 进入应用用例。应用层调用领域规则及基础设施。领域层接收业务数据并完成权限和事实判定，数据库、网络与模型访问由基础设施处理。文档的解析、审批和发布共用现有版本和权限门禁，HyDE 的许可及模型预算由问答用例核对。

PostgreSQL 保存在线状态、任务队列和 outbox。原件存入 MinIO 的 `ecom-private` 桶或本机私有目录。Kafka 的 `ecom.tasks` 分发任务编号，Worker 可通过数据库轮询领取。文档检索先圈定当前用户可读资料，再融合 Elasticsearch BM25 和 pgvector ANN 结果，并保留本地检索路径。

新增目录时登记职责。路径调整时同步更新接口、脚本、评测和测试的引用。CI 使用实际提交树校对目录登记。
