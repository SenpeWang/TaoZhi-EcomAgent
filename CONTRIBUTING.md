# 企业工作台开发与贡献规范

本文件是仓库公开的开发与协作规范。`AGENTS.md` 是维护者的协作约定，随仓库版本化；克隆仓库不依赖该文件即可开发。目标仓库为 `https://github.com/SenpeWang/TaoZhi-EcomAgent.git`。本仓库由仓库所有人维护，完成本地检查后直接向 main 提交和推送，不要求工作分支、PR 或 Squash。外部使用者通过 Issue 反馈问题和建议。

## 环境与前端风格

使用 Python 3.10、Node.js 24 和 PostgreSQL 18；运行时不覆盖主机系统安装。后端锁为 requirements.lock.txt，前端锁为 web/package-lock.json。首次开发安装：

~~~bash
cd web
npm ci
cd ..
make install-hooks
~~~

ESLint Flat Config 组合 JavaScript、Vue 3 与 TypeScript 推荐规则，eslint-config-prettier 消除格式冲突；Prettier 统一两空格、单引号、无 JS 分号、尾随逗号、100 字符行宽及 LF。CSS 和 JSON 仍遵循各自语法。工具基线为 ESLint 9.39.5、Prettier 3.9.9、commitlint 21.2.3，插件和传递依赖以锁文件为准。参考 [Vue 官方配置](https://eslint.vuejs.org/user-guide/)。

自建 Vue 组件文件名和声明使用 PascalCase，根组件允许 App.vue。函数和变量使用 camelCase，类型使用 PascalCase；业务常量使用 UPPER_SNAKE_CASE。接口字段保留服务端契约，不强制将 JSON 字段重命名。Python 使用 snake_case、PascalCase、类型注解和显式导入，不对现有后端做全量格式重排。

具名业务函数和导出函数必须有中文 JSDoc；有输入、返回值或异常时解释含义。会话、CSRF、API、权限、文档、任务与审核必须说明拒绝路径及边界，不写照抄代码的注释。Python 关键业务用中文 docstring 和类型注解。禁止使用 any 隐去不确定响应、全局关闭 lint 规则或忽略权限失败；网络数据先以 unknown 接收并在边界校验，再进入接口类型。

~~~bash
make lint-web         # ESLint、组件文件名和格式检查
make format-web       # 显式整理前端；不自动暂存
make check-format     # 仅格式检查
make check-commits    # origin/main..HEAD 的提交信息与内容
make check-commits COMMIT_BASE=<基准提交> COMMIT_HEAD=HEAD
make test             # 共享测试环境 config/test.env 的离线回归
make migrate-test     # 把测试库迁移到最新版本
make build-web        # 类型检查、生产构建与原子切换
~~~

缺少 Node 或前端依赖时明确失败。钩子不会联网安装、格式化、自动暂存或 stash；请自行修复后选择需要提交的文件。

## 结构与企业契约同步

| 改动 | 同一提交必须同步 |
| --- | --- |
| 新增、删除、移动目录 | STRUCTURE.md 登记及职责、README 结构、引用、脚本、忽略规则和测试 |
| 接口及字段 | 请求/响应类型、中文错误码、会话与 CSRF、权限范围、前端调用和拒绝测试 |
| 角色、资料与审核 | [权限与文档治理](documentation/access-control/)、密级/组织矩阵、泄漏、撤权及运行中任务验收 |
| 配置与依赖 | 无秘密示例、版本锁、运行时、启动、部署和 CI |
| 数据库结构 | 新 Alembic 迁移、现有数据兼容、升级验证及恢复条件 |
| Agent、队列与来源 | 预算、重试、取消、租约、执行版本及权限变化后的恢复检查 |
| 脚本、命名与端口 | 所有调用、Makefile、Docker Compose、启动项和使用说明 |

`AGENTS.md` 随仓库版本化并作为协作约定；本机专属手册与个人文档放在 `docs/private/`，该目录整体不进入 Git，公开文档不链接私有目录。目录必须有单一职责，不建 temp/new/final 平行业务目录。页面和 API 承接输入输出，统一业务与授权服务负责状态变化；新功能不得另建身份或权限旁路。事务、并发版本、资源上限、超时、幂等和有限重试是业务设计的一部分。

## Git 提交规范（Conventional Commits）

提交必须遵循 Conventional Commits 1.0.0 规范，格式为：

```text
<type>(<scope>): <subject>

<body>
```

### 1. Type 类型清单

| type | 说明 |
| :--- | :--- |
| `feat` | 新增业务功能、智能体能力或核心架构组件 |
| `fix` | 修复缺陷、安全逻辑或异常 |
| `refactor` | 代码重构，非修 bug 也非加功能 |
| `perf` | 提升计算、检索或执行性能 |
| `style` | 代码格式调整（空格、格式等），非 CSS |
| `docs` | 仅文档或注释变更 |
| `build` | 构建系统、依赖包或锁定文件修改 |
| `ci` | CI 配置文件与自动化工作流脚本变动 |
| `test` | 新增或修正测试用例 |
| `chore` | 杂务（如 .gitignore、目录登记等） |
| `revert` | 撤销提交 |

### 2. Scope 规则（三层级）

Scope 取 `<项目>/<模块>/<脚本>` 或 `<模块>/<脚本>`，主题写最高归属模块层：
- 项目层：仓库名或顶层项目，如 `ecom`；
- 模块层：一级子系统，如 `enterprise`、`agents`、`web`、`retrieval`、`pipeline`；
- 脚本层：具体改动组件或文件。

### 3. 树状多层级 Body（核心规范）

从实际 `git diff` 出发，自底向上归并缩进，精准陈述每个组件的具体变更，杜绝“更新代码/优化”等空泛描述：

```text
feat(enterprise/cache): 引入微秒级向量语义缓存中台

src/taozhi/infrastructure/cache/
   semantic_cache.py: 实现基于点积相似度的本地向量缓存与 LRU 淘汰
   worker.py: 在任务执行前注入缓存命中判定与 ACL 权限重验
tests/
   test_memory.py: 补充缓存击中与权限撤销边界测试
```

破坏性变更可用 `!` 或 `BREAKING CHANGE:`，正文必须说明兼容影响和迁移办法。历史初始化提交保留，不为规范重写已发布历史。

## 维护者提交与推送工作流（核心纪律）

### 核心推送铁律：向 GitHub 推送前必须主动询问 Senpe 并获得确认！

维护者在向远程仓库推送任何改动时，必须严格执行以下工作流：

1. **本地快检四类全部通过（必要前提）**：
   - 仓库边界：`make check-repository`；
   - 语法与规范：`make check`、`make lint-web check-format`；
   - 生产构建：`make build-web`；
   - 配置契约：`ECOM_MODE=test .venv/bin/python -m pytest tests/test_test_environment.py -q`。
   全量回归不在本地复刻：`make migrate-test`、`make test`、`make evals-agent-replay`、`make check-commits` 由 GitHub Actions 在干净容器与独立 `ecom_test` 库执行。
2. **严防敏感数据外流**：
   - 严禁提交 `.env*`（仅允许 `.env.example`）、凭据与私钥、`docs/private/`；`data/` 与 `docs/` 的演示资料可以入库，`config/test.env` 不含任何口令。
3. **向 Senpe 索取推送许可**：
   - 将完整 commit 信息（Header + 树状 Body）及待推送的分支、提交哈希展示给 Senpe；
   - 只有在 Senpe 明确回复“可以 / 推送 / 同意”后，方可执行 `git push`；未经允许绝不擅自推送！
4. **推送后三方一致性校验**：
   - 推送后核验本机 `HEAD`、`origin/main` 和 GitHub 远端 `main` 分支 SHA 完全一致；
   - 实时监控 GitHub Actions CI 构建流程，确保 Exit Code 为 0（全绿灯）。

```bash
git status --short
git remote -v
git fetch origin --prune
git switch --no-overwrite-ignore main
git merge --ff-only origin/main
# 按实际差异暂存改动
git add src/ web/ tests/ README.md
python3 scripts/check_development.py staged
git diff --cached --stat
# 规范提交
git commit -m "docs(governance): 全面更新开发与推送规范"
# 推送前本地二次核验
make check-commits
# ⚠️ 询问 Senpe 确认后方可执行推送
git push origin main
# 推送后核对 SHA
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
```

维护者直接提交 main，使用标准 Conventional Commits 标题；提交和推送钩子必须通过，推送后等待该提交的 CI。比较本机 main、origin/main 和 GitHub main 的 SHA，三者应一致。代码推送不等于部署。

禁止强推、覆盖已发布历史、使用 --no-verify 或 git add -f 绕过检查。非快进时先 fetch 并阅读远端变化：远端仅领先则正常快进；双方都有提交则使用 git merge --no-commit origin/main，解决冲突并验证后创建符合规范的合并提交，保留双方历史，再正常推送，不覆盖并发更新。目标分支分叉或无法快进时先检查再决定，不覆盖本机规则。

用户已经授权推送时直接完成所需门禁、提交和推送，不重复索取确认；没有发布授权则完成本地维护，不擅自发布。连接器或网页创建提交也须先执行同等检查，并保留父提交、核验预期远端 SHA。认证使用连接器、SSH Agent 或系统凭据管理，不在 URL 中放令牌。

## 门禁检查对象与 CI

- pre-commit：先检查 **完整 Git 索引** 的公开边界，再从 Git blob 导出暂存快照，对该快照执行组件、ESLint 与 Prettier；最后检查暂存 diff 空白。未暂存修复不参与结果。临时目录成功和失败均清理。
- commit-msg：检查消息中的秘密与 Conventional Commits，不回显原始标题、正文或秘密。
- pre-push：限定目标仓库，逐次检查全部新增提交的文件树、目录、秘密、提交消息及前端快照。中间提交的资料或密钥即使最后删除也拒绝。
- CI：由 main 的 push 触发，四个并行作业分别执行前端规范与生产构建、仓检与提交规范、Python 语法与 Agent 合成轨迹回放、测试库迁移与离线回归；全部经 Makefile 目标与本地同源，测试环境取自 `config/test.env`、口令由 `PGPASSWORD` 注入，不调用付费模型、不读取真实资料、不自动部署。
- 提交消息按数据和文件参数处理，不拼入 shell。CI 最小权限 contents: read；动作固定提交。
- 每份克隆须 make install-hooks，Git 不会自动启用仓库钩子。文档与 CI 不能替代 GitHub 服务端保护设置；只有实际配置才可宣称强制启用。本期保持已有仓库设置。

## 公开资料与反馈边界

凭据与私钥、私有 .env、`docs/private/`、老板初始化身份、数据库转储、备份、运行日志、依赖与构建产物禁止提交；`data/` 与 `docs/` 中的演示资料可以入库，`AGENTS.md` 随仓库版本化。业务文件不得搬到源码、公开说明或测试中绕过检查。测试只能自行构造最小合成事实。已跟踪私有文件用 git rm --cached 移出 Git，保留本机原件；仅修改 .gitignore 不会停止跟踪已有文件。发现历史秘密停止推送并安排凭据轮换，删除现文件不能清除历史，不自行强推。

提交正文和交付说明记录问题、改动、影响、验证、文档同步及迁移风险。Issue 提供 Bug、功能、文档和使用问题四类表单。反馈前移除密码、API Key、Cookie、客户资料与机密正文，截图也要脱敏。

## 发布与恢复

数据库结构只追加迁移，不改已应用版本。发布记录提交、依赖锁、迁移、维护窗口和验证；恢复需考虑新业务写入及旧代码兼容，不能假定旧库可无损覆盖。默认不生成 bak、源码副本或自动快照。业务数据备份按用户明确安排执行，Git 只保存公开代码历史。当前单机多进程部署不宣称高可用。

### 协作约定与本机私有文档

`AGENTS.md` 随仓库版本化，作为维护者的协作约定；本机专属运行手册、个人说明与历史归档放在 `docs/private/`，该目录整体忽略。历史提交中出现过的本机维护文件保留原样，检查不再为其保留兼容名单；删除跟踪不会清除旧提交中的内容，也不会改动其他分支。

日常维护在 main 进行。切换分支直接用 `git switch`；若目标分支分叉、无法快进或仍跟踪被忽略的本机文件，停止切换并检查，不覆盖本机规则。
