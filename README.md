# 手机配件电商企业多智能体工作台

面向商品资料查询、适配咨询、客服售后与运营协作的中文工作台。Vue 3 提供登录、文档库、智能问答、组织管理和审核页面；FastAPI 统一执行权限；LangGraph 按问题调度专业智能体；PostgreSQL 与独立 Worker 保存任务、审核、来源及流程检查点。

本仓库公开代码、维护规则和软件文档。**整个 `data/`、企业资料、私有配置、本机维护文件 `AGENTS.md` 与个人学习文件 `项目说明.md` 均不上传。** 克隆代码不会获得原部署的资料、账号或数据库。

## 主要功能

- 公司 → 部门 → 小组 → 成员的组织结构，支持组织范围内的员工、组长、老板，以及负责系统管理的管理员。
- 员工级、组长级、老板级资料；密级与组织范围共同决定可见性，支持有期限的单文档读取和下载授权。
- 文档受限草稿、上传解析、版本、审批发布、私有附件与可核验引用。
- 分开的资料入库与业务问答流程，商品规格、产品适配、客服售后、运营选品、库存供应链和经营分析专家按需参与。
- 身份与来源在任务、审核、发布、历史答案和追问中重新验证，撤权后不能继续使用旧答案。
- 同源 HttpOnly 会话 Cookie、写操作 CSRF 校验、独立环境、审计及任务崩溃恢复。

| 身份 | 默认可见范围 | 默认操作 |
| --- | --- | --- |
| 员工 | 公司共享和本组员工级资料、单独授权资料 | 阅读、提问、查看本人任务 |
| 组长 | 上述资料与所负责团队的组长级资料 | 本范围资料维护和业务审核 |
| 老板 | 本公司业务资料，包括老板级机密 | 公司业务读写、机密及关键权限审批 |
| 管理员 | 公司共享资料、另外获得授权的资料 | 组织、账号、配置和系统审计 |

管理员不能自行取得业务机密；组长不能因任职而管理其他组；审核不会把审核人的权限借给提问者。完整规则见 [权限说明](docs/ACCESS_CONTROL.md)。

## 工作流

~~~mermaid
flowchart LR
  A[登录身份与权限] --> B[问题计划]
  B --> C[授权资料与只读业务查询]
  C --> D[按需专业智能体]
  D --> E[事实与引用核验]
  E --> F[必要人工审核]
  F --> G[发布前重验权限]
  G --> H[中文答案与可访问引用]
~~~

入库流程独立完成上传校验、解析、归属与密级确认、切片、来源登记、索引和发布审核。检索先限定授权候选再排序；切片、关系和记忆继承来源版本及权限。进度只输出中文阶段，核验完成前不流出正文。

老板级资料默认禁止外部模型；经老板明确允许 AI 处理并允许本次问题使用外部 AI 后才能调用。Sensenova 使用 OpenAI 兼容接口并保持直连，密钥只保存在部署环境。

## 技术与目录

| 部分 | 实现 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Element Plus（简体中文） |
| 后端 | FastAPI，`/api/v2`，统一会话和权限 |
| Agent | LangGraph，按需专家、证据核验、审核门禁 |
| 持久化 | PostgreSQL，Alembic 迁移 |
| 任务 | 独立 Worker，租约、心跳、执行版本与有限重试 |
| 部署 | Linux 用户目录运行时、Supervisor、单机多进程 |

~~~text
src/ecom_copilot/enterprise/   在线业务、权限与工作流
src/ecom_copilot/             共享能力和隔离的离线模块
web/src/                     中文工作台、类型与同源 API
.github/workflows/           CI 与 PR 标题、提交范围检查
.github/ISSUE_TEMPLATE/      四类中文反馈表单
.githooks/                   提交快照、消息和推送门禁
migrations/                  数据库迁移
scripts/                     部署、检查和维护命令
tests/                       单元测试与企业边界验收
docs/                        公开架构、部署与贡献说明
data/                        本机业务资料和运行产物（忽略）
~~~

新增目录、接口、配置、迁移或脚本必须同步说明和验证。详细目录职责见 [项目结构](docs/STRUCTURE.md)，开发提交流程见 [贡献约定](docs/CONTRIBUTING.md)。本机维护约束和个人学习说明由维护者在部署主机保留，不作为公开仓库依赖。

## 本地部署

当前验证环境为 Linux x86_64、Python 3.10、Node.js 24、PostgreSQL 18。不需要服务器 Docker 权限，安装脚本在用户目录准备运行时；主机需提供编译工具和 Python。首次安装步骤与正式初始化见 [部署说明](docs/ENTERPRISE_V3.md)。

~~~bash
git clone https://github.com/SenpeWang/test.git
cd test
bash scripts/install_runtime.sh
cp .env.example .env
# 在本机 .env 中填写模型配置；不要提交密钥
.venv-v3/bin/python scripts/manage_database.py provision
ECOM_V3_CONFIG="$PWD/.env.v3-demo" .venv-v3/bin/python scripts/manage_database.py demo
.venv-v3/bin/python scripts/configure_services.py
make build-web
make start
~~~

演示入口为 `http://127.0.0.1:18501/`。独立演示环境的 `admin`、`staff`、`leader`、`boss` 密码均为 `123456`，正式环境拒绝此口令。演示初始化只建立明确标注的组织与权限样例；商品文件需自行通过文档库上传。没有资料或模型配置时，系统说明证据或配置不足。

远端部署可通过 SSH 转发访问；使用自己的部署账号、地址和 SSH 端口：

~~~bash
ssh -N -L 18501:127.0.0.1:18501 用户名@服务器地址
~~~

浏览器中的 `127.0.0.1` 指客户端电脑，转发连接需保持打开。正式服务默认监听回环地址；对外开放时应配置 HTTPS 和安全 Cookie，不能直接暴露演示环境。

## 开发与验证

~~~bash
make install-hooks    # 启用提交与推送门禁，每份克隆执行一次
make check-repository # 检查已跟踪文件、秘密及目录登记
make lint-web         # ESLint、组件命名与格式检查
make format-web       # 显式整理前端，不自动暂存
make check-format     # 格式检查
make check-commits    # 新增提交的格式和公开内容
make test             # 独立测试库的离线回归
make build-web        # TypeScript 检查与生产构建
make verify-browser   # 演示入口四身份浏览器验收
~~~

依赖锁为 `requirements.v3.lock.txt` 和 `web/package-lock.json`。CI 验证提交范围、PR 标题、仓库边界、目录登记、ESLint、Prettier、组件命名、类型构建、数据库迁移和独立测试库的离线回归；不读取真实资料或调用付费模型。权限与泄漏边界的验收范围见 [验证说明](docs/VALIDATION.md)。

## 当前边界

真实订单、库存和财务系统尚未接入，显示“未接入”；连接器只读，不自动退款、改价或修改订单。单机多进程已实现，多机高可用、企业 SSO、真实业务接入与异机备份需要单独部署验收。演示或测试事实不能作为经营数据。

## 贡献与反馈

前端采用 ESLint Flat Config 和 Prettier 的统一基线，组件使用 PascalCase，关键业务函数写中文 JSDoc。提交遵循 Conventional Commits：`类型(可选模块): 中文说明`，标题最多 100 字符。

维护者建立工作分支，外部贡献者先 Fork；完成检查、规范提交和推送后创建 PR。CI 通过，由维护者审阅并 Squash 合并，PR 标题作为最终提交标题。Squash 后工作分支与 main 哈希不同是正常现象，一致性核验针对同一分支。具体命令与同步矩阵见 [贡献规范](docs/CONTRIBUTING.md)。

反馈表单包括 Bug、功能建议、文档改进和使用问题；请提供场景、复现或预期结果，并去除凭据、客户信息及机密正文。仓库钩子需要主动安装，服务端保护以 GitHub 实际设置为准。
