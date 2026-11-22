# TaoZhi-EcomAgent 项目运维与协作约定

本文件为跨会话核心约束。未经用户许可，严禁擅自修改本文件。

## 1. 变更确认与权限（最高优先级铁律）
- **修改与推送必须确认**：新增或修改任何代码、配置、文档（含 AGENTS.md 本身），以及推送到 GitHub，必须先向用户（Senpe）展示摘要并获得明确许可。
- **范围**：同时适用于 Windows 工作区与 WSL 项目目录覆盖的全部文件。
- **安全校验**：不能上传大模型的APIkey,如果检查到必须拒绝或者脱敏!

## 2. 路径与执行环境
- **项目路径**：`<项目根目录>`（WSL Ubuntu-24.04）。
- **执行方式**：一律通过约定的免密 SSH 通道执行命令（修改文件勿直接走 Windows UNC 避免权限拒绝）：
  `ssh <免密通道> '<cmd>'`
- **Python 环境**：embedding 等重依赖用 Conda `llm` 环境解释器；项目服务用 `.venv`。

## 3. Git 提交规范（Conventional Commits & Angular 标准）
- **遵循标准**：对齐国际主流 **Conventional Commits 1.0（Angular 规范）** 与 `@commitlint/config-conventional`，由 `web/commitlint.config.cjs` 和 `.githooks/commit-msg` 强制校验。
- **原子提交（Atomic Commits）**：一笔 Commit 只完成一个清晰独立的目标（按功能模块/配置/评测/文档合理拆分小粒度提交），避免大杂烩式捆绑提交导致 GitHub 目录历史混杂、难以精准回滚。
- **Header 结构**：`<type>(<scope>): <subject>`（≤100 字符，英文 scope/动词小写规范，subject 必须含中文且末尾无句号）。
- **限定 Type（11 类）**：`feat|fix|refactor|perf|style|docs|build|ci|test|chore|revert`。
- **Body 结构与折行**：Header 后空一行写正文，优先采用**标准 Markdown 多级列表（`- ` / `  - `）**，从 `git diff` 出发自底向上按模块/文件简述具体变更与原因（严禁“优化代码/更新”等空泛描述）；每行严格控制 **≤100 字符**，避免触犯 `commitlint` 的 `body-max-line-length` 拦截。
- **Footer 约定**：破坏性变更须在 Header 加 `!`（如 `feat(api)!: ...`）并在 Footer 添加 `BREAKING CHANGE: <影响与迁移说明>`；关联单号可用 `Refs: #...` / `Closes: #...`。

## 4. 仓库边界与零敏感外流
- **绝对禁止提交**：`.env*`（除 `.env.example`）、密钥凭据（API Key / Token / 私钥 / 数据库密码）、`docs/private/` 私有文档目录以及未在 `STRUCTURE.md` 登记的目录。
- **资料无敏感限制**：`data/` 与 `docs/` 为演示与评测内容，除 API Key 与私钥凭据外均可入库；本机运维手册、个人说明与历史归档统一存放于被忽略的 `docs/private/`；密钥由 `scripts/check_repository.py` 扫描拦截。

## 5. 门禁类型与推送纪律

- **门禁分七类，本地只跑快的四类**：
  1. 仓库边界：`make check-repository`（敏感信息、未登记目录、大文件与二进制）
  2. 语法与规范：`make check`（Python 编译）、`make lint-web check-format`（前端 ESLint/Prettier）
  3. 生产构建：`make build-web`（TypeScript 类型检查与产物构建）
  4. 配置契约：`make test` 中的纯配置断言（`tests/test_test_environment.py`，秒级）
  5. 全量回归：`make test`（离线权限、流程、开发门禁与评估单元测试）
  6. 迁移与轨迹回归：`make migrate-test`、`make evals-agent-replay`
  7. 提交范围：`make check-commits`
- **权威判定在 GitHub CI**：第 5-7 类需要干净依赖、独立测试库与真实容器，统一由远端 Actions 执行；本地不复刻远端环境，不建镜像、不摆第二套变量、不搭临时工作区，第 1-4 类绿灯即可推送。
- **推送后必须跟进**：`gh run list --limit 1` 取 run id，`gh run watch <id> --exit-status` 等到变绿才算完成；失败必须贴出失败步骤与真实报错原文再修，不得重跑碰运气，也不得删测试、加 skip、改 `continue-on-error` 或关工作流让红灯变绿。
- **推送确认**：推送前向 Senpe 完整展示 commit 摘要与 SHA 哈希；严禁私自推送、绕过钩子（`--no-verify`）或强推（`--force`）。
- **远端推送通道（只用 SSH）**：WSL 直连 `github.com` 不通（`curl` 返回 FAILED），推送统一经本地代理走 SSH：在 `~/.ssh/config` 的 `Host github.com` 段配置本地代理后直接 `git push origin main`；不使用 HTTPS 令牌旁路。直连失败症状为 pack 写满 100% 后无回执、最终 `sideband packet` 断开。

## 6. 严禁硬编码与虚假伪造
- **禁止硬编码与死路径**：严禁在代码中硬编码任何主机绝对路径（如主机绝对路径）、固定条数限制或伪造参数；所有路径必须基于模块位置、标记文件（如 `STRUCTURE.md`）或环境变量动态自适应解析。
- **严禁虚假内容与无中生有**：面向用户或前端广播的任何阶段思考、状态摘要、指标数据、采集数量（如货源条数、出厂价、验真结论等），必须 100% 真实反射底层智能体与工具调用的实际运行结果；绝对禁止通过写死文案向用户播报编造的虚假数字、提前断言或伪造成功。数据为 0 或抓取失败必须如实反映并客观说明备选降级方案。
- **项目中不能硬编码,硬编码信息需要我确认**

## 7. 测试与评测使用正式账号
- **测试/演示/评测登录统一使用正式业务账号**：`staff`（员工）、`leader`（组长）、`owner`（老板）、`admin`（系统管理员），不再单独保留 `syn_*` 测评账号。
- **统一口令**：`taozhi2026eval`（14 位纯小写字母+数字，定义于 `evals/dataset/seed.py` 的 `EVAL_PASSWORD`），由 `python -m evals.dataset.seed` 校准口令与夹具权限。

## 8. 代码注释规范（仅保留 Docstring，清理冗余注释）
- **只写 Docstring**：Python 模块/类/函数与前端 TS/JS 导出的接口/函数统一只写规范 Docstring（Google Style / JSDoc），说明职责、参数与返回值。
- **删除冗余注释**：禁止编写复述代码字面含义的行内注释、已废弃的旧逻辑注释及被注释掉的死代码；审查或修改文件时，发现此类冗余注释应一并删除。

## 9. 容器网络与本地代理约定（WSL Demo 部署）
- **强制使用宿主机网络**：本项目当前为 WSL2 单机 Demo/评测部署，`docker-compose.yml` 中的 `api`、`worker`、`migrate` 容器必须保持 `network_mode: host`，直接共享 WSL 宿主机网络与本地代理出口访问海外模型（禁止改回隔离 Bridge 网段导致海外通道超时）。
- **中间件本机回环绑定**：`postgres`（`15432`）、`minio`（`19000`）、`kafka`（`19092`）、`elasticsearch`（`19200`）、`embedding`（`18555`）统一仅绑定 `127.0.0.1` 供宿主机网络容器连接，不对外网暴露端口。

## 10. 单一名称原则：重构不留别名

- **一个概念只有一个名称**：重命名字段、参数或返回结构时，全部调用点一次性改完，代码里不保留第二名称、只读派生属性或双写字段。
- **禁止兼容分支**：不得用旧字段是否存在的判断维持两种写法并存。
- **过渡必须登记**：确需分阶段迁移时，在同一次提交说明里写明剩余步骤与截止条件，并尽快移除过渡代码。
- **外部契约保持原样**：数据库列名、环境变量名、第三方接口字段属于数据源，不做改名；同一份内部数据不得以两个名称对外暴露。

## 11. 本地与远端 CI 同源

- **测试环境只有一份定义**：`config/test.env` 是唯一测试环境，`load_config()` 在 `ECOM_MODE=test` 时自动解析它；本地与远端 CI 因此取到同一份取值，禁止再把测试变量抄进 `.github/workflows/ci.yml`。
- **凭证只走宿主环境**：`config/test.env` 不含任何口令，`load_config()` 拒绝内嵌口令的测试连接串；`PGPASSWORD` 由本地 `.env` 或 CI 服务占位符注入，连接串与口令分离。
- **命令集只有一份定义**：CI 直接调用 `make check-repository / check / lint-web / check-format / build-web / migrate-test / test / evals-agent-replay`，与推送前校验完全一致；新增步骤先加 Makefile 目标，不允许在 workflow 里手写等价命令。
- **测试模式固定独立库**：`config/test.env` 的 DSN 必须指向 `/ecom_test`，端口与 CI 服务映射一致（`15432`）；用例若切 production 模式必须自行注入 DSN，不得依赖宿主恰好存在某个变量。
- **推送后必须跟进结论**：推送后用 `gh run list --limit 1` 取 run id，`gh run watch <id> --exit-status` 等到变绿才算完成；失败必须贴出失败步骤与真实报错原文再修，不得重跑碰运气。
- **本地不复刻远端环境**：需要干净依赖、独立测试库或真实容器的验证一律交给 GitHub CI；本地只跑第 1-4 类快检，避免重复搭建环境拖慢节奏。
- **禁止妥协式变绿**：不得通过删测试、加 skip、改 `continue-on-error`、关闭工作流或放宽校验让红灯变绿。
