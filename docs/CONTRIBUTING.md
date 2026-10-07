# 企业工作台开发与贡献规范

本文件是仓库公开的开发与协作规范。维护者本机的 AGENTS.md 仅供本地维护，不进入 Git；克隆仓库不依赖该文件即可开发。目标仓库为 `https://github.com/SenpeWang/test.git`。维护者在本仓库创建工作分支；外部贡献者先 Fork。采用单人维护、工作分支与 PR、维护者审阅和 Squash 合并。

## 环境与前端风格

使用 Python 3.10、Node.js 24 和 PostgreSQL 18；运行时不覆盖主机系统安装。后端锁为 requirements.v3.lock.txt，前端锁为 web/package-lock.json。首次开发安装：

~~~bash
export PATH="$HOME/.local/share/ecom-v3/runtime/node/bin:$PATH"
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
make test             # 独立 test 库离线回归
make build-web        # 类型检查、生产构建与原子切换
make verify-browser   # 四种身份及同浏览器换人后的残留检查
~~~

缺少 Node 或前端依赖时明确失败。钩子不会联网安装、格式化、自动暂存或 stash；请自行修复后选择需要提交的文件。

## 结构与企业契约同步

| 改动 | 同一提交必须同步 |
| --- | --- |
| 新增、删除、移动目录 | STRUCTURE.md 登记及职责、README 结构、引用、脚本、忽略规则和测试 |
| 接口及字段 | 请求/响应类型、中文错误码、会话与 CSRF、权限范围、前端调用和拒绝测试 |
| 角色、资料与审核 | ACCESS_CONTROL.md、密级/组织矩阵、泄漏、撤权及运行中任务验收 |
| 配置与依赖 | 无秘密示例、版本锁、运行时、启动、部署和 CI |
| 数据库结构 | 新 Alembic 迁移、现有数据兼容、升级验证及恢复条件 |
| Agent、队列与来源 | 预算、重试、取消、租约、执行版本及权限变化后的恢复检查 |
| 脚本、命名与端口 | 所有调用、Makefile、Supervisor、启动项和使用说明 |

本机 AGENTS.md 与项目说明.md 同步维护，但不进入 Git；公开规范和文档不链接或依赖这些本机文件。目录必须有单一职责，不建 temp/new/final 平行业务目录。页面和 API 承接输入输出，统一业务与授权服务负责状态变化；新功能不得另建身份或权限旁路。事务、并发版本、资源上限、超时、幂等和有限重试是业务设计的一部分。

## 提交规范

格式为 `类型(可选模块): 中文变更说明`，标题最多 100 字符。允许：

`feat、fix、refactor、docs、test、build、ci、chore、style、perf、revert`。

~~~text
feat(documents): 增加资料版本对比
fix(auth): 修复改密后的会话撤销
chore(governance): 统一开发门禁和贡献流程
feat(api)!: 调整文档授权接口

BREAKING CHANGE: 移除旧字段，客户端须使用新接口；部署前完成兼容迁移。
~~~

破坏性变更可用 ! 或 BREAKING CHANGE:，正文必须说明兼容影响和迁移办法。历史初始化提交保留，不为规范重写已发布历史。一个提交处理一个可解释的主题；正文写原因、影响和验证。参考 [Conventional Commits 规范](https://www.conventionalcommits.org/zh-hans/v1.0.0/)。

## 工作分支、PR 与 Squash

~~~bash
git status --short
git remote -v
git fetch origin --prune
git switch --no-overwrite-ignore -c feat/document-history origin/main
# 修改代码、说明并完成必要验证
git add web/src/DocumentHistory.vue docs/STRUCTURE.md README.md
# 上面只是选择文件的例子，须按实际差异明确暂存；不盲目 git add .
python3 scripts/check_development.py staged
git diff --cached --stat
git commit -m "feat(documents): 增加资料版本对比"
git push -u origin HEAD
# 在 GitHub 创建 PR，按模板填写，等待 CI 和维护者审阅
~~~

维护者审阅后使用 **Squash and merge**；PR 标题需符合提交规范，作为最终 squash 提交标题。禁止强推、覆盖已发布历史、使用 --no-verify 或 git add -f 绕过检查。非快进先 fetch 并理解变化，整合后重新验证，再正常推送。

合并后：

~~~bash
git fetch origin --prune
# 从工作分支更新本机 main，只接受正常快进，不强推或重写历史
git fetch origin main:main
git switch --no-overwrite-ignore main
git merge --ff-only origin/main
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
~~~

比较的是同一分支本地 HEAD、远端跟踪引用与 GitHub 引用。Squash 后工作分支与 main 的哈希通常不同，这是正常现象。未合并 PR 时核验工作分支与 origin/工作分支一致。代码推送不等于部署。

用户已经授权推送时直接完成所需门禁、提交和推送，不重复索取确认；没有发布授权则完成本地维护，不擅自发布。连接器或网页创建提交也须先执行同等检查，并保留父提交、核验预期远端 SHA。认证使用连接器、SSH Agent 或系统凭据管理，不在 URL 中放令牌。

## 门禁检查对象与 CI

- pre-commit：先检查 **完整 Git 索引** 的公开边界，再从 Git blob 导出暂存快照，对该快照执行组件、ESLint 与 Prettier；最后检查暂存 diff 空白。未暂存修复不参与结果。临时目录成功和失败均清理。
- commit-msg：检查消息中的秘密与 Conventional Commits，不回显原始标题、正文或秘密。
- pre-push：限定目标仓库，逐次检查全部新增提交的文件树、目录、秘密、提交消息及前端快照。中间提交的资料或密钥即使最后删除也拒绝。
- CI：独立执行公开内容、目录、提交范围、PR 标题、ESLint、Prettier、组件命名、TypeScript 构建、Alembic 与 PostgreSQL 18 独立测试库回归；不读取真实资料、调用付费模型或自动部署。
- PR 标题经环境变量和标准输入传入校验，不拼入 shell。CI 最小权限 contents: read；动作固定提交。
- 每份克隆须 make install-hooks，Git 不会自动启用仓库钩子。文档与 CI 不能替代 GitHub 服务端保护设置；只有实际配置才可宣称强制启用。本期保持已有仓库设置。

## 公开资料与反馈边界

整个 data/、任意目录和大小写的 AGENTS.md、项目说明.md、私有 .env、老板初始化身份、凭据、数据库、附件、备份、运行日志、依赖和构建产物禁止提交。业务文件不得搬到源码、公开说明或测试中绕过检查。测试只能自行构造最小合成事实。已跟踪私有文件用 git rm --cached 移出 Git，保留本机原件；仅修改 .gitignore 不会停止跟踪已有文件。发现历史秘密停止推送并安排凭据轮换，删除现文件不能清除历史，不自行强推。

PR 模板记录问题、改动、影响、验证、文档同步及迁移风险。Issue 提供 Bug、功能、文档和使用问题四类表单。反馈前移除密码、API Key、Cookie、客户资料与机密正文，截图也要脱敏。

## 发布与恢复

数据库结构只追加迁移，不改已应用版本。发布记录提交、依赖锁、迁移、维护窗口和验证；恢复需考虑新业务写入及旧代码兼容，不能假定旧库可无损覆盖。默认不生成 bak、源码副本或自动快照。业务数据备份按用户明确安排执行，Git 只保存公开代码历史。当前单机多进程部署不宣称高可用。

### 本机维护文件的规则切换

AGENTS.md 已改为本机文件，名称大小写和所在目录均不影响禁传规则。Git 忽略、暂存检查、新增提交检查及 CI 都拒绝重新加入此文件。

切换前已经发布的提交 `5018289`、`2b071e5` 保留原历史；检查仅对这两个确定提交兼容旧维护文件路径，仍检查其秘密、其他禁传资料和目录登记。该兼容不适用于任何新提交或暂存内容，不允许扩大历史名单来放行未发布内容。删除跟踪不会清除旧提交中的文件，也不会自动改动尚未合并的其他分支。

维护主机切换分支使用 git switch --no-overwrite-ignore，避免旧分支覆写被忽略的 AGENTS.md。合入修正后，在工作分支先用 git fetch origin main:main 正常快进本机 main，再切换；若目标仍跟踪维护文件、分支分叉或无法快进，停止切换并检查，不能覆盖本机规则。
