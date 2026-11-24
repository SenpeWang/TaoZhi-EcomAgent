# 数据库模型

部署版本的表结构以 Alembic 迁移为准。初始定义记载通用实体和约束，后续迁移增加向量索引、任务事件、轨迹与评测表。[基础表](../../src/taozhi/server/schema.sql) · [版本迁移](../../migrations/versions/)

## 数据归属

| 业务域 | 主要表 | 读取规则 |
| --- | --- | --- |
| 身份和组织 | `tenants`、`users`、`org_nodes`、`memberships`、`sessions` | 会话绑定主体版本。用租户和组织关系推导访问范围。 |
| 文档与检索 | `documents`、`document_versions`、`chunks`、`knowledge_mentions`、`embedding_cache` | 文档版本保留原件指针和提取正文。切片记录原文页码，已发布文档按来源、密级和个人授权过滤。 |
| 审批与授权 | `approvals`、`document_grants`、`metric_grants` | 审批和明确授权均按租户处理，读取时复核对象及期效。 |
| 异步任务 | `jobs`、`task_events`、`task_outbox`、`task_reviews`、`worker_heartbeats` | 任务按租户与发起人归属，执行版本和租约用于隔离重试。结果与事件按当前资料权限读取。 |
| 经营数据 | `metrics`、`products`、`skus`、`channels`、`orders`、`after_sales`、`stock` | ChatBI 通过获授权的指标模板执行只读查询，结果保留指标来源。实时订单和库存也可经企业网关读取。 |
| 审计与轨迹 | `audit_events`、`model_calls`、`task_spans` | 通过任务编号、执行版本和 Trace ID 查调用步骤、用量和诊断。 |
| 评测 | `eval_datasets`、`eval_profiles`、`eval_runs`、`eval_case_results`、`eval_run_reports`、`eval_baselines`、`eval_audit_logs` | 数据集按 `version`/`suite`/`content_sha256` 登记版本，`archived` 区分历史；批次记录发起人、`scope`、`dataset_version`、算法版本（`profile_id`）、门禁与主指标；`eval_run_reports` 存原始报告对象位置与 sha256；`eval_baselines` 按数据集版本 + 口径 + 执行方式保留 active 基线与历史；`eval_audit_logs` 记录受控写入。批次、明细与数据集同事务写入，可按数据集版本和批次追溯。 |

系统管理员可按发起人查本租户轨迹。资料正文和业务结果另行校验权限。[访问规则](../../src/taozhi/infrastructure/persistence/access_repo.py) · [轨迹查询](../../src/taozhi/application/services/trace_service.py)

## 文档版本与执行记录

```mermaid
erDiagram
    TENANTS ||--o{ USERS : "账号"
    TENANTS ||--o{ DOCUMENTS : "资料"
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : "修订"
    DOCUMENT_VERSIONS ||--o{ CHUNKS : "逐页切片"
    USERS ||--o{ JOBS : "发起"
    JOBS ||--o{ TASK_EVENTS : "阶段与流式输出"
    JOBS ||--o{ TASK_SPANS : "执行节点"
    USERS ||--o{ EVAL_RUNS : "评测归属"
    EVAL_DATASETS ||--o{ EVAL_RUNS : "数据集版本"
    EVAL_PROFILES ||--o{ EVAL_RUNS : "算法版本"
    EVAL_RUNS ||--o{ EVAL_CASE_RESULTS : "逐例判定"
    EVAL_RUNS ||--o| EVAL_RUN_REPORTS : "原始报告对象"
    EVAL_RUNS ||--o{ EVAL_BASELINES : "锁定基线"
```

- `documents.current_version` 指向当前版本。`acl_version` 用于判断历史引用及缓存的权限有效性。原件由附件存储适配器按租户保存。
- `chunks.embedding` 使用 pgvector，页面引用按切片的文档版本和页码展示。文本检索和向量检索使用同一份可读文档范围。[向量迁移](../../migrations/versions/006_pgvector.py) · [文档仓储](../../src/taozhi/infrastructure/persistence/document_repo.py)
- 测试库 `ecom_test` 与生产库同构，连接串固定在 `config/test.env`，用 `make migrate-test` 迁移；集成测试会清空该库的租户与业务数据。
- `jobs.run_version` 区分同一任务的尝试。SSE 通过递增事件编号恢复。`task_spans` 保存节点的父子关系与经过筛选的诊断，评测可以引用对应 Trace ID。[事件迁移](../../migrations/versions/017_task_output_events.py) · [轨迹迁移](../../migrations/versions/014_task_spans.py) · [评测中心迁移](../../migrations/versions/020_evaluation_center.py)
- 评测中心 V3 结构：`eval_datasets` 含 `suite`、`content_sha256`、`metadata`、`archived`；`eval_runs` 含 `scope`（full/quick/retrieval/adversarial/behavior）与 `dataset_version`；`eval_profiles` 以 `version_no`、`digest`、`git_sha`、`dataset_version` 标识不可变算法版本，并对 `digest` 与 `version_no` 建唯一索引；`eval_run_reports` 存原始报告对象键、sha256、字节数；`eval_baselines` 存 active + 历史，同一数据集版本 + 口径 + 执行方式只允许一条 active；`jobs.kind` 含 `evaluation`，由 Worker 执行评测；`eval_audit_logs.action` 含 `purge_data`，记录数据清理。[评测中心 V3 迁移](../../migrations/versions/023_eval_center_v3.py) · [评测版本库迁移](../../migrations/versions/022_eval_profiles_and_rag.py) · [清理审计迁移](../../migrations/versions/024_eval_audit_purge.py)
