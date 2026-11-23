# 部署与运维

生产环境使用 Linux、Docker Engine 和支持 `env_file.required` 的 Compose v2。API 和 Worker 共用应用镜像，向量服务单独加载模型。迁移成功且依赖服务健康后，API 和 Worker 才启动。[Compose 编排](../../docker-compose.yml) · [GPU 覆盖配置](../../docker-compose.gpu.yml)

## 配置部署主机

| 配置 | 核对内容 |
| --- | --- |
| PostgreSQL 与 MinIO | 按配置示例在部署主机创建私有 `.env`，填写 `ECOM_PG_PASSWORD`、`ECOM_MINIO_ACCESS_KEY` 和 `ECOM_MINIO_SECRET_KEY`。数据库密码会拼入连接 URL，可选用随机十六进制值。 |
| 向量和精排 | `ECOM_MODELS_DIR` 指向模型父目录。启用对应服务时准备 `bge-small-zh-v1.5` 和 `bge-reranker-v2-m3`。Compose 默认使用 CPU，可叠加 GPU 配置。 |
| 大模型与业务网关 | 按需设置 `BASE_URL`、`API_KEY` 和 `MODEL`；备用通道为 `SENSENOVA_OVERSEAS_*`、`DOTS_*`、`BAILIAN_*`，候选顺序依次为海外商用通道、Dots、阿里云百炼、国内主备。实时库存与订单使用 `BUSINESS_API_URL` 和 `BUSINESS_API_TOKEN`。 |
| 中间件 | 根据部署目标设置 `ECOM_MINIO_ENABLED`、`ECOM_KAFKA_ENABLED` 和 `ECOM_ES_ENABLED`。环境示例给出功能开关，容器启动顺序由 Compose 编排定义。 |
| 构建镜像 | 慢速网络下在 `.env` 里设置 `PIP_INDEX_URL`、`NPM_REGISTRY` 与 `APT_MIRROR`，`make rebuild` 会用它们加速后端依赖、前端依赖与系统包安装。 |
| 持久化 | 保证容器可写 `data/`。生产资料保存在对象存储或私有附件目录，部署主机保管数据库和运行记录。 |

[运行配置示例](../../.env.example) · [数据目录说明](../../data/README.md)

网关基础地址使用 HTTPS。应用请求 `/stock/{sku_id}` 或 `/orders/{order_id}` 时，带上 `X-Org-Tag`、`X-User-Id` 与令牌。响应使用 `tenant_id`、`node_id`、对应对象编号和带时区的 `as_of`，订单状态字段为 `status`。返回字段会按可访问范围裁剪。[业务网关契约](../../src/taozhi/infrastructure/business_gateway.py)

## 对外入口与端口

| 服务 | 默认宿主机入口 |
| --- | --- |
| 工作台/API | `127.0.0.1:18080` |
| PostgreSQL | `127.0.0.1:15432` |
| MinIO | `127.0.0.1:19000` |
| Kafka | `127.0.0.1:19092` |
| Elasticsearch | `127.0.0.1:19200` |
| 嵌入服务 | `127.0.0.1:18555` |

公网接入使用 HTTPS 反向代理，设置 `ECOM_COOKIE_SECURE=true`。SSE 需要关闭代理缓冲。下面是 Nginx TLS 虚拟主机中的转发配置。

```nginx
client_max_body_size 25m;

location / {
    proxy_pass http://127.0.0.1:18080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}
```

## 启动顺序与自愈

Compose 的 `depends_on` 只在 `docker compose up` 时生效；宿主机重启后容器由 `restart: unless-stopped` 拉起，彼此没有顺序保证。因此 API 与 Worker 的启动命令先经过依赖闸门 [wait_for_startup.py](../../scripts/wait_for_startup.py)：数据库、MinIO、Kafka、Elasticsearch 与向量服务都探测通过后才替换成真正的服务进程；`docker compose up`、`docker start` 与重启策略三种拉起方式都适用。

| 场景 | 行为 |
| --- | --- |
| 数据库崩溃恢复 | 闸门按探测间隔重试连接，日志行以 `[启动闸门]` 开头；恢复后自动继续启动 |
| 向量服务 GPU 未就绪 | 嵌入服务在 `EMBED_GPU_BOOT_GRACE_SECONDS`（默认 180 秒）内重试 CUDA 与探测，超过宽限期才按启动失败退出 |
| 健康检查宽限期 | 各服务 `start_period` 覆盖自身恢复窗口，恢复期显示 `health: starting` 而不是 `unhealthy` |
| 闸门超时 | 默认 600 秒内依赖仍不可用则以非零码退出，交由重启策略重试，日志列出未就绪依赖 |

可调参数写进 `.env` 即对容器生效：`ECOM_STARTUP_WAIT_SECONDS`（闸门整体超时）、`ECOM_STARTUP_PROBE_INTERVAL`（探测间隔）、`EMBED_GPU_BOOT_GRACE_SECONDS`（GPU 启动宽限期）。

宿主机重启后无需手工干预：`docker compose ps` 里的 `health: starting` 属正常，依赖恢复后自动转 `healthy`。代码升级包含数据库迁移时仍需执行 `make start`（或 `docker compose up -d`）触发 `migrate`，重启策略只拉起常驻容器，不补跑一次性迁移。

## 首次部署与验收

```bash
docker compose config --quiet
make rebuild
docker compose ps -a
curl -fsS http://127.0.0.1:18080/api/health
curl -fsS http://127.0.0.1:18555/health
```

1. 确认 `migrate` 退出码为 `0`，API 和向量服务返回健康状态。
2. 准备目标租户及公司组织根，执行 `docker compose exec api python scripts/manage_database.py bootstrap-owner` 创建首位老板账号。
3. 用测试身份核对资料的阅读与下载权限，提交知识问答并在轨迹中台检查执行节点。首次使用第三方 PDF 时核对页码和引用。 [问答验收](../knowledge-base/)

## 评测中心与评测任务

评测中心把数据集版本、算法版本、批次与基线落库，并可由工作台触发真实评测。数据与报告口径见 [评测体系](../../evals/README.md) 与 [评估基准](../../evals/BENCHMARK.md)，本节只讲部署与运维。

### 运行前置

| 条件 | 核对方式 |
| --- | --- |
| 数据库已迁移到 `023_eval_center_v3` | `migrate` 容器退出码为 `0`，`eval_run_reports`、`eval_baselines` 表存在 |
| API 与 Worker 在线 | `curl -fsS http://127.0.0.1:18080/api/health`；Worker 日志有任务认领与租约心跳 |
| RAG 夹具已灌库 | `PYTHONPATH=src .venv/bin/python -m evals.dataset.seed`（`--verify` 只校验），语料、身份、权限与向量需与生产库一致 |
| 检索与向量服务可用 | `curl -fsS http://127.0.0.1:18555/health`；启用 Elasticsearch 时对应实例健康 |
| 对象存储可用 | MinIO 正常时原始报告写私有桶；不可用会告警并回退本地私有目录，仅适合临时排障 |
| Worker 镜像含评测代码 | 镜像包含 `evals/`、`src/taozhi/application/evaluation/` 与 023 迁移；升级后 `make rebuild` 重建 API 与 Worker |

### 回填与自检

```bash
make evals-center-register                 # 登记 V1 数据集（幂等）
make evals-center-backfill                 # 回填历史数据集、报告与基线（幂等）
make evals-center-check                    # 数据集 sha、基线唯一性与报告对象自检
```

以上目标默认使用 `ECOM_MODE=production`，需在能连生产库与对象存储的主机执行。`BACKFILL_ARGS="--reset"` 会先清空评测中心写入表，属破坏性操作，只在明确重建时使用。

### 触发与观测

- 触发：`POST /api/evaluations/runs/trigger`，请求体含 `eval_kind`、`dataset_id`、`scope`、`mode`，可选 `profile_id`、`note`、`mark_baseline`。仅老板/系统管理员可调用，成功返回 `202` 与 `job_id`、`audit_id`；同租户已有排队或执行中的评测任务时返回 `409`。
- 任务：创建后 `jobs.kind=evaluation`，Worker 启动运行器子进程并回报阶段进度；`GET /api/evaluations/jobs/{job_id}` 返回 `state`、`stage`、`progress`、`result` 与 `error`。
- 日志：运行器输出在 `data/runtime/eval_jobs/<job_id>/runner.log`，进度 JSONL 在同目录 `progress.jsonl`；完成后报告经对象存储入库，批次号回填任务结果。
- 取消与超时：`POST /api/evaluations/jobs/{job_id}/cancel` 终止评测子进程组；单次执行超过 `ECOM_EVAL_TIMEOUT_SECONDS`（默认 3600 秒）记为失败。评测任务按租户串行，不并发执行。
- 审计：`GET /api/evaluations/audit-logs` 查询登记数据集/算法版本、触发、发布与设基线留痕，仅老板/系统管理员可读。

`ECOM_GIT_SHA` 用于镜像内没有 `git` 命令时记录算法版本的代码提交，建议构建时注入当前提交；容器内能读取 Git 元数据时以实际 `HEAD` 为准。

门禁未通过不等于任务失败：报告仍会入库并标记 `gate_passed=false`，任务按 `completed` 结束；只有运行器崩溃、超时、缺报告或报告校验失败才记 `failed`。

## 常见运维入口

| 情况 | 查看位置 |
| --- | --- |
| 任务长期排队 | 查看 `docker compose ps -a` 的迁移状态，再查看 `docker compose logs --tail=100 worker` 的认领和续租记录。 |
| 宿主机重启后服务未就绪 | 先看 `docker compose logs --tail=100 api` 的 `[启动闸门]` 等待项；数据库恢复期显示 `health: starting` 属正常。 |
| 工具、模型 API 或资料核验失败 | 在任务的「执行轨迹」查节点、问题日志和 Trace ID，再关联进程日志。 |
| 评测任务排队或失败 | 先查 `GET /api/evaluations/jobs/{job_id}` 的阶段与结果，再看 `data/runtime/eval_jobs/<job_id>/runner.log`；门禁未过是正常结果，不是任务失败。 |
| 备份与恢复演练 | 数据库和本地私有附件可使用仓库备份脚本，MinIO 对象另行备份。 |

[备份与恢复脚本](../../scripts/backup_database.py) · [数据库模型](../database/)
