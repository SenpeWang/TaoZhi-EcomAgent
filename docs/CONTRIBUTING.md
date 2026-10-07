# 开发、提交与推送

维护约束以根目录 [AGENTS.md](../AGENTS.md) 为准。目标仓库固定为 `https://github.com/SenpeWang/test.git`；不得把业务资料同步到其他仓库。

## 修改前

1. 检查 `git status --short`、当前分支、现有差异及远端地址，保护尚未提交的其他工作。
2. 已有仓库先 `git fetch origin --prune`，阅读远端变化；不覆盖已有历史，不执行强推。
3. 从当前 `origin/main` 建立 `feat/<主题>`、`fix/<主题>`、`docs/<主题>` 或 `chore/<主题>` 分支。初次导入空仓库可以建立 main；用户明确要求当前分支直推时依授权执行。
4. 每份克隆执行 `make install-hooks`。Git 不会自动启用仓库内的钩子；CI 仍独立执行检查。

## 修改与文档一起完成

| 改动 | 必须同步 |
| --- | --- |
| 新增、删除、移动目录 | STRUCTURE.md 的目录登记及职责、README 结构，引用、脚本、忽略规则和测试 |
| 新增或修改接口 | 请求和响应契约、错误码、身份与组织范围、前端调用、接口及权限测试 |
| 业务权限或资料政策 | ACCESS_CONTROL.md、权限矩阵、泄漏与撤权测试、审核流程 |
| 依赖或配置 | 依赖锁、空值配置示例、部署说明、启动与 CI |
| 数据库结构 | 新 Alembic 迁移、升级/兼容/恢复步骤和迁移验证 |
| Agent、队列或来源 | 状态、预算、重试、来源与恢复约束，以及中断和权限变化验收 |
| 命名、脚本、端口 | 所有引用、Makefile、Supervisor、启动项、部署和使用说明 |

个人学习说明 `项目说明.md` 如存在也应同步，保留在本机；公开文档不能链接到它或 data/ 中的证据。

## 提交门禁

~~~bash
git status --short
git diff --stat
git add README.md AGENTS.md docs/ scripts/ src/ tests/ migrations/ web/src/
# 根据实际变更明确选择文件，配置文件另行选择，不盲目暂存整个工作目录
python3 scripts/check_repository.py --staged
git diff --cached --check
git diff --cached --stat
make test  # 按改动范围选择；权限和跨模块改动必须回归
git commit -m "docs(governance): 完善目录和提交约束"
~~~

`check_repository.py` 检查实际暂存 blob 或提交历史，拒绝整个 data/、个人笔记、私有环境、凭据、数据库、资料附件、备份、依赖目录、构建产物、大文件与疑似密钥；报错只显示路径和类别，不回显秘密。目录登记未同步也会失败。

忽略规则不能移除已被跟踪的文件。必要时用 `git rm --cached -- 文件` 移出 Git，保留本机内容，并检查最终提交树。不要用 `git add -f` 或 `--no-verify` 绕过规则。文件先提交再删除仍保留在历史中，推送钩子会检查即将发送的各次提交。

提交遵循 `类型(模块): 中文说明`，类型包括 feat、fix、refactor、docs、test、build、ci、chore。一个提交只处理一个可解释的主题；正文说明原因、影响、验证及需要迁移的步骤。不把账号密码、资料标题、客户信息或日志正文放进提交和 PR 描述。

## 推送逻辑

1. 用户已要求推送时，在既有授权范围内完成检查、提交和推送，不重复要求确认。未要求发布时可完成本地修改与提交，不擅自推送。
2. 推送前重新 fetch，检查目标分支和远端地址，确认提交基于最新远端历史。工作分支通过 PR 合入 main，避免未经审阅的生产改动。
3. `git push -u origin HEAD` 触发 pre-push；它检查全部新增提交及其文件，而不只检查当前工作区。
4. 非快进失败先理解并合并或整理本分支的未发布提交，重新验证后正常推送。禁止 `--force`、删除远端分支和重写已发布历史。
5. 连接器推送或网页提交也必须先完成相同门禁；通过 GitHub API 创建提交时保留父提交，并使用预期远端 SHA 更新分支，拒绝并发覆盖。
6. 推送成功核验远端 commit SHA 与本地一致，并检查 CI 状态；报告仓库、分支、提交和实际验证结果。CI 未完成时说明状态。
7. 首次导入空仓库允许 main 初始化；发现远端已有内容先阅读并保留，不能用项目导入覆盖其他人的文件。

认证使用 GitHub 连接器、SSH Agent 或系统凭据管理，不把 PAT 放进远端 URL、源码或 shell 输出。若发现秘密已进入历史，先停止传播并说明需要撤销/轮换凭据；删除文件不能清除历史，不自行强推重写。

## 发布与恢复

main 的推送只发布代码，不等于生产部署。部署记录目标提交、依赖锁、迁移版本和验证结果；明确维护窗口、兼容策略与失败回退条件。不能用旧代码版本回退已经不兼容的新数据结构。

代码历史由 Git 保存。默认不创建 bak、源码副本或快照；业务数据不靠 Git 备份，需要明确的数据运维方案。当前备份工具只在明确请求时执行。

CI 使用独立 PostgreSQL 服务库的方式参考 [GitHub PostgreSQL 服务容器文档](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)，与实际用户目录部署环境分开。
