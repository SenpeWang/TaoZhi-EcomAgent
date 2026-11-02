# TaoZhi-EcomAgent 部署清单（腾讯轻量云 Lighthouse）

> 适用场景：把当前 WSL 开发机上的全套服务搬到一台 Lighthouse 实例，对外提供「前端 + 应用 API」。
> 数据（知识库 / 运行时）与 LLM API（外部按量付费）的关系见对话图示；本清单只列「怎么落地」。
> 所有事实均来自 `supervisor.conf`、`scripts/start_services.sh`、`enterprise/config.py`、`web/vite.config.ts`、`.env` / `.env.production` 实际核对，非凭记忆。

---

## 一、选型与前提

| 项 | 建议 | 说明 |
|---|---|---|
| 实例规格 | **4C8G 起**，最小 2C4G | embedding + reranker 在 CPU 上能跑但吃内存；ES 另需 1G 堆 |
| 系统 | Ubuntu 24.04 | 与开发机一致，避免 glibc / Python 差异 |
| GPU | **不需要** | 但当前 supervisor 写死 `EMBED_DEVICE=cuda:0`，必须改成 `cpu`（见第九节） |
| 出网 | 必须 | 服务器要能访问 `token.sensenova.cn`（LLM 推理） |

---

## 二、服务拓扑与端口（来自 supervisor.conf，已核对）

| Program | 命令 / 端口 | 必选 | 备注 |
|---|---|---|---|
| database-guard | `scripts/watch_database.py` | ✅ | 内部用 `pg_ctl` 拉起 PostgreSQL（数据目录 `~/.local/share/ecom-agent/postgres`，端口 15432） |
| embedding | `scripts/embedding_server.py` :18555 | ✅ | bge-small-zh-v1.5(512d) + bge-reranker-v2-m3；`EMBED_DEVICE=cuda:0` 需改 cpu |
| worker-production | `ecom_copilot.enterprise.worker` | ✅ | 处理 jobs，无对外端口 |
| api-production | uvicorn :18080（仅 127.0.0.1） | ✅ | 业务 API，**必须用 Nginx 反代暴露** |
| elasticsearch | :9200，堆 1G | ⚪ | 默认 `ECOM_ES_ENABLED=false` 未启用；supervisor 仍起，需装 ES 或禁 program |
| kafka | :9092 | ⚪ | 默认 `ECOM_KAFKA_ENABLED=false`；同上 |
| minio | :19000 / 19001 | ⚪ | 默认 `ECOM_MINIO_ENABLED=false`；同上 |

**最小可用集 = PG + embedding + worker + api**（关闭 ES / Kafka / MinIO 可大幅缩小 footprint 与内存占用）。

---

## 三、依赖与二进制（不在仓库内，需单独就位）

开发机上的这些路径**不会**随 git 走，部署时必须重新准备：

- `.venv/`（api / worker 的 Python 环境）→ 服务器上 `python -m venv .venv && pip install -r requirements*.txt`
- `miniconda3/envs/llm`（embedding 用的 Python 解释器）→ 或直接在 `.venv` 里装 embedding 依赖，统一环境
- 模型 `/home/sp/models/bge-small-zh-v1.5`（约 130MB）
- middleware 目录 `~/.local/share/ecom-agent/middleware/` 下的 **ES 9.2.3、Kafka 2.13-4.1.2、MinIO** → 仅在你启用对应服务时才需要

---

## 四、两份配置文件（缺一不可，均含密钥，禁止入库）

项目同时读两份 env：仓库根 `.env`（LLM / 鉴权）+ 由 `ECOM_ENV_FILE` 指向的 `.env.production`（企业链路）。supervisor 给 api / worker 注入 `ECOM_ENV_FILE=/.../.env.production` 和 `PYTHONPATH`。

**`.env`（LLM + 鉴权，变量名已核对）：**
- `BASE_URL`（当前 `https://token.sensenova.cn/v1`）
- `API_KEY`（sensenova 密钥，产生费用）
- `MODEL` / `LLM_FAST_MODEL` / `LLM_STRONG_MODEL` / `LLM_VISION_MODEL`
- `JWT_SECRET`（会话签名，**必须换强随机值**）
- `AUTH_ENABLED`、`APP_ENV`、`OFFLINE_MODE`、`SYNTHETIC_FALLBACK_ENABLED`、`LLM_TIMEOUT`

**`.env.production`（ECOM_*，必填项已核对）：**
- `ECOM_DATABASE_URL`（含 PG 端口，决定连哪）
- `ECOM_TENANT_CODE`（默认 `mofa`）
- `ECOM_MODE=production`
- 可选开关：`ECOM_ES_ENABLED` / `ECOM_KAFKA_ENABLED` / `ECOM_MINIO_ENABLED`（默认均 false）
- 其余 `ECOM_*` 在 `enterprise/config.py` 均有默认值，不填即用默认

> 部署动作：用 `scp` 把两份文件传到服务器（权限 `600`），**不要** `git add`。

---

## 五、前端构建 + Nginx 反代

```bash
cd web
npm install
npm run build        # 输出 web/dist/（vite 默认 outDir，已确认 web/dist 存在）
```

`vite.config.ts` 仅配置了 dev 代理（`/api → 127.0.0.1:18080`），**生产无反代配置**，需自行加 Nginx：

```nginx
server {
    listen 80;
    server_name your-domain;          # 或公网 IP
    root /path/to/TaoZhi-EcomAgent/web/dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:18080;   # api 仅监听 127.0.0.1
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

> ⚠️ 若上 HTTPS：`.env.production` 设 `ECOM_COOKIE_SECURE=true`，并确认前端 base 路径。

---

## 六、数据迁移（关键，均不在 git 内）

两类数据，**都不随代码走**：

1. **知识库（PG `ecom_production`：423 chunks / 131 docs + ES `chunks` 索引）**
   - 方案 A（推荐演示）：服务器新建空 PG，跑 `python -m evals.dataset.seed` 重新灌库。
   - 方案 B（保真）：把开发机 `~/.local/share/ecom-agent/postgres` 数据目录整体拷到服务器同路径（注意 PG 大版本一致）。
2. **运行时状态**
   - 记忆库 JSON：默认落在 `{项目根}/data/runtime/memory`（现用向上探测项目根 + `ECOM_MEMORY_DIR`），随项目目录走，但内容是本地沉淀，按需迁移。

---

## 七、防火墙（Lighthouse 控制台）

| 规则 | 端口 | 说明 |
|---|---|---|
| 放行业务 | 80 / 443 | 只暴露 Nginx |
| **不放** | 18080 / 18555 / 9200 / 9092 / 19000 | 仅 127.0.0.1 内网，公网绝不开放 |
| 可选 | 6006 | Phoenix 观测，仅调试时临时开放，用完即关 |

> api-production 已绑定 `127.0.0.1`，即便防火墙误开也不直接暴露；ES / Kafka / MinIO 同理只监听 loopback。

---

## 八、部署后验证

```bash
# 1. 七件套状态（最小集 4 个 RUNNING）
.venv/bin/python -m supervisor.supervisorctl -c ~/.local/share/ecom-agent/supervisor.conf status

# 2. 业务健康检查（需 ECOM_ENV_FILE 同 supervisor 设置）
curl -s --noproxy '*' http://127.0.0.1:18080/api/health

# 3. 端到端：前端能打开 + 发一条问答能召回 chunks + 拿到 LLM 答案
```

---

## 九、部署前必须改的 3 个硬伤（否则必挂）

1. **`EMBED_DEVICE="cuda:0"` → `cpu`**：supervisor.conf `[program:embedding]` 写死 GPU。Lighthouse 无 GPU，不改则 embedding 进程起不来，全部检索 / 精排失效。
2. **middleware 二进制缺失**：ES / Kafka / MinIO 路径在 `~/.local/share/ecom-agent/middleware/`，仓库不带。若保留 supervisor 里这 3 个 program 又没装好，会反复重启报错。两条路二选一：
   - 装齐 middleware（体积大、占内存）；
   - **或在部署版 supervisor.conf 里删掉这 3 个 program**，靠 config.py 默认 `false` 回落到 pgvector / DB 轮询 / LocalBlobStore（推荐演示）。
3. **密钥与端口**：`JWT_SECRET` 换强随机；确认 `ECOM_DATABASE_URL` 指向服务器 PG 端口；`API_KEY` 在服务器有额度且能出网。

---

## 十、下一步（待你拍板）

- **B**：连 Lighthouse，按本清单真正部署（先把未提交改动 commit 并跑回归，再动手）；
- 或先生成**部署版 supervisor.conf**（去掉 ES / Kafka / MinIO 三个 program、embedding 改 cpu）+ **Nginx 配置模板**，减少上云手误。
