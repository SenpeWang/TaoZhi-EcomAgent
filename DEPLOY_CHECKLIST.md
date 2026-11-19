# TaoZhi-EcomAgent 云端部署清单（Docker Compose）

> 适用场景：把当前仓库整套服务部署到云服务器（如腾讯轻量云 Lighthouse 或独立 GPU/CPU 主机），对外提供「前端 + 应用 API」。
> 架构基线：全官方基础镜像 + Docker Compose 多容器编排（`Dockerfile`、`docker-compose.yml`、`docker-compose.gpu.yml`）。

---

## 一、服务器选型与前提

| 项 | 建议 | 说明 |
| --- | --- | --- |
| 实例规格 | **4C8G 起**（无内置中间件最小集可 2C4G） | `embedding` + `reranker` 在 CPU 下需足够内存；ES 默认堆上限 `-Xms1g -Xmx1g` |
| 系统 | Ubuntu 24.04 + Docker Engine + Compose v2 | 容器屏蔽宿主机 Python/Node/PG 差异 |
| GPU | **可选** | 无 GPU 直接使用 `docker-compose.yml` 默认 `EMBED_DEVICE=cpu`；有 GPU 安装 `nvidia-container-toolkit` 后叠加 `docker-compose.gpu.yml` |
| 出网 | 必须 | `api` 与 `worker` 需能访问大模型端点（如 `token.sensenova.cn`） |

---

## 二、容器拓扑与端口

| 服务 | 镜像来源 | 端口策略 | 持久化 / 挂载 |
| --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg18` | `127.0.0.1:15432:5432` | `pg_data:/var/lib/postgresql` |
| `minio` | `minio/minio:latest` | 仅容器内网 `:19000` | `minio_data:/data` |
| `kafka` | `apache/kafka:4.1.2`（KRaft） | 仅容器内网 `:9092` | `kafka_data:/var/lib/kafka/data` |
| `elasticsearch` | `docker.elastic.co/elasticsearch/elasticsearch:9.2.3` | 仅容器内网 `:9200` | `es_data:/usr/share/elasticsearch/data` |
| `embedding` | `Dockerfile` (`embedding` 目标) | 仅容器内网 `:18555` | `${ECOM_MODELS_DIR:-../models}:/models:ro` |
| `migrate` | `Dockerfile` (`app` 目标) | 一次性执行 `alembic upgrade head` | — |
| `api` | `Dockerfile` (`app` 目标，内置 `web/dist`) | `127.0.0.1:18080:18080` | `./data:/app/data` |
| `worker` | `Dockerfile` (`app` 目标) | 无端口 | `./data:/app/data` |

> 老板下载 MinIO 原件默认经 `api` 内部读取并安全代理输出，不向公网暴露 MinIO 端口，也不重定向至内网域名。

---

## 三、配置文件（仅在目标主机生成，禁止入库）

1. 复制 [.env.example](file:///home/sp/TaoZhi-EcomAgent/.env.example) 为 `.env`（`chmod 600 .env`），至少填写：
   - LLM 凭据：`BASE_URL`、`API_KEY`、`MODEL`
   - 数据库与对象存储口令：`ECOM_PG_USER`、`ECOM_PG_PASSWORD`、`ECOM_PG_DB`、`ECOM_MINIO_ACCESS_KEY`、`ECOM_MINIO_SECRET_KEY`
   - 模型目录：`ECOM_MODELS_DIR`（宿主机存放 `bge-small-zh-v1.5` 与 `bge-reranker-v2-m3` 的目录）
   - 若服务器具备 NVIDIA GPU：追加 `COMPOSE_FILE="docker-compose.yml:docker-compose.gpu.yml"`
2. `docker-compose.yml` 已在 `environment:` 中自动覆写容器间通信地址（`postgres:5432`、`minio:19000`、`ECOM_MINIO_SECURE="false"`、`kafka:9092`、`http://elasticsearch:9200`、`http://embedding:18555`），避免域名或回环错配。

---

## 四、Nginx 反代与防火墙

- **云服务器防火墙**：只放行 `80 / 443`；`18080` 与 `15432` 在 Compose 中仅绑定 `127.0.0.1`，绝不直接对公网开放。
- **Nginx 反向代理示例**：

```nginx
server {
    listen 80;
    server_name your-domain;
    client_max_body_size 50m;
    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;   # 保持 SSE 流式任务进度实时推送
    }
}
```

> 若启用 HTTPS，请在 `.env` / `.env.production` 设置 `ECOM_COOKIE_SECURE=true`。

---

## 五、启动与部署后验收

```bash
# 1. 构建并后台启动全栈服务
docker compose up -d --build

# 2. 检查容器健康状态（migrate 须为 Exited (0)，其余均为 healthy/running）
docker compose ps

# 3. 验证 API 与向量服务健康探针
curl -fsS http://127.0.0.1:18080/api/health
docker compose exec -T api curl -fsS http://embedding:18555/health
```
