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

## 常见运维入口

| 情况 | 查看位置 |
| --- | --- |
| 任务长期排队 | 查看 `docker compose ps -a` 的迁移状态，再查看 `docker compose logs --tail=100 worker` 的认领和续租记录。 |
| 工具、模型 API 或资料核验失败 | 在任务的「执行轨迹」查节点、问题日志和 Trace ID，再关联进程日志。 |
| 备份与恢复演练 | 数据库和本地私有附件可使用仓库备份脚本，MinIO 对象另行备份。 |

[备份与恢复脚本](../../scripts/backup_database.py) · [数据库模型](../database/)
