# 本地演示

下面从全新检出目录启动一个只监听本机的测试工作台，使用独立的 `ecom_test` 数据库和合成资料。示例环境为 Linux，需安装 Git、Python 3.10（含 `venv`）、Node.js 24（含 npm）和 Docker。这个示例只用 Docker 运行 PostgreSQL，API 和 Worker 从源码运行。

**1. 获取源码并准备前后端**

```bash
git clone https://github.com/SenpeWang/TaoZhi-EcomAgent.git
cd TaoZhi-EcomAgent
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
npm --prefix web ci
npm --prefix web run build
```

**2. 创建本地数据库与 `.env`**

在新检出目录执行一次。以下命令生成随机数据库口令，只把口令写入仅当前用户可读的 `.env`，并把数据库绑定到 `15432` 端口。测试库连接串固定在 `config/test.env`，本地与远端 CI 共用同一份，换口令不需要改连接串。

```bash
umask 077
cp .env.example .env
chmod 600 .env
demo_password="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(24))')"
docker run -d --name taozhi-demo-db -p 127.0.0.1:15432:5432 \
  -e POSTGRES_USER=ecom_test -e POSTGRES_DB=ecom_test \
  -e POSTGRES_PASSWORD="$demo_password" pgvector/pgvector:pg18
printf 'PGPASSWORD=%s\n' "$demo_password" >> .env
unset demo_password
docker exec taozhi-demo-db pg_isready -h 127.0.0.1 -U ecom_test -d ecom_test
```

首次初始化可能需要片刻。重复执行最后一条就绪检查，看到 `accepting connections` 后，在仓库根目录运行迁移、导入测试账号与资料，并启动 API。`ECOM_MODE=test` 时配置层自动读取 `config/test.env`，口令来自 `.env` 的 `PGPASSWORD`。

```bash
make migrate-test
ECOM_MODE=test .venv/bin/python -c 'from scripts.manage_database import seed_test; seed_test()'
ECOM_MODE=test .venv/bin/python -m uvicorn taozhi.server.api:app --host 127.0.0.1 --port 18080
```

**3. 启动 Worker 并体验第一条轨迹**

在第二个终端进入同一仓库根目录，启动处理异步任务的进程。

```bash
ECOM_MODE=test .venv/bin/python -m taozhi.server.worker
```

打开 [本机工作台](http://127.0.0.1:18080)。使用测试租户 `mofa`、员工账号 `staff` 和测试口令 `123456` 登录，在知识问答中提交「店铺运营组负责什么工作？」随后打开「轨迹中台」，选择这次提问，查看「执行记录」。切换为同一测试租户的 `admin` 账号，可按发起人查到员工的这次提问。未配置模型凭据时仍可查看检索步骤。要观察一次真实模型调用，在私有配置中填入 `BASE_URL`、`API_KEY` 和 `MODEL`，重启 API、Worker 后重新提问。[测试夹具](../../scripts/manage_database.py) · [配置示例](../../.env.example)

体验结束时，在两个终端分别按 `Ctrl+C`，再运行 `docker stop taozhi-demo-db`。下次可用 `docker start taozhi-demo-db` 复用测试库。

## 开发检查

准备锁定依赖后，在仓库根目录运行检查。企业集成测试会清空 `config/test.env` 指定的测试库的租户和业务数据，测试模式只允许连接 `ecom_test` 库；远端 CI 跑同一批 `make` 目标，取值也来自同一份 `config/test.env`。

```bash
make check-repository
make lint-web check-format
make test
```

提交遵循 Conventional Commits，摘要含中文，标题最多 100 字符。可用 `make install-hooks` 启用本地检查。[前端规范](../../web/eslint.config.js) · [提交规范](../../web/commitlint.config.cjs) · [GitHub CI](../../.github/workflows/ci.yml)
