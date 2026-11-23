# evals/ — 评估体系（数据集 · 指标 · 运行器）

本目录是项目**唯一**的评估资产存放处。数据集、评估代码、基线、报告全部在这里；
`scripts/` 只放部署与运维脚本，不出现任何评估逻辑。

## 目录结构

```
evals/
├── rag_eval/                    ★企业知识库 RAG 三层评测运行器（检索 · 对抗 · 行为）
│   ├── run.py                   独立评测执行器（--progress-jsonl / --report-path / 分层开关）
│   ├── publish.py               RAG 报告入库入口（兼容旧命令，委托 evals/center）
│   └── 测评记录.md              知识文档 RAG 评测基线、改动对比与归因档案
├── agent_eval/                  ★电商真·自主多智能体（Supervisor Loop）质效评测体系
│   ├── harness.py               调研图执行采集与合成轨迹重评
│   ├── judges.py                执行状态、工具轨迹、交付、事实证据与干预判定
│   ├── cases.py                 调研基准用例
│   ├── runner.py                CLI 批跑、报告、版本对照与门禁
│   ├── publish.py               Agent 报告入库入口（兼容旧命令，委托 evals/center）
│   └── 数据集设计.md            数据集构建、验收与归因说明
├── center/                      ★评测中心唯一入库编排（数据集登记 · 报告发布 · 基线 · 回填 · 自检）
│   ├── cli.py                   register / publish / baseline / backfill / check 子命令
│   ├── datasets.py              数据集版本描述符校验、用例装载与幂等登记
│   ├── publish.py               报告校验 → 批次/逐例落库 → 原始报告对象 → 审计与基线
│   ├── profiles.py              算法版本快照与摘要复用
│   ├── baselines.py             基线锁定与历史留痕
│   └── backfill.py              历史数据集、报告与基线的幂等回填
├── dataset/                     独立评测数据包：规格 → 确定性生成 → 幂等灌库（见其 README）
├── datasets/                    数据集（随代码进 Git，按版本目录只读留档、可追溯、可 diff）
│   └── v1_20261123/             当前基准版本
│       ├── dataset.json         版本描述符：套件、用例数、逐文件 sha256、可用口径与方式
│       ├── agent/               Agent 基准用例、标准回放轨迹、定向反例与网页边界样本
│       └── rag/                 180 例主集、42 篇语料、18 例对抗集与 manifest
├── runtime_snapshot.py          运行时快照：数据集版本、Git 提交、Prompt 源摘要与算法版本
├── metrics.py                   指标实现（全项目唯一口径）
├── sync_phoenix.py              Arize Phoenix 平台双轨数据同步中枢
├── BENCHMARK.md                 口径唯一来源：指标定义·判定协议·报告 schema·数据契约
├── reports/                     运行产物（Git 忽略）
├── baseline.json                唯一基线快照，随代码进 Git
└── tests/                       评估代码自身的单元测试
```

## RAG 三层评估

| 层 | 评什么 | 判定方式 | 数据来源 |
|---|---|---|---|
| retrieval | 检索质量：Hit@1/3/8、Precision@k、Recall@k、nDCG@k、MRR、延迟 | 规则判定（命中预期标题集合） | `datasets/v1_20261123/rag/cases.jsonl` + `corpus_docs.jsonl` 全量 |
| adversarial | 安全：注入、越权、撤权、金丝雀泄漏 | 全自动规则判定（机读规则，零人工复核；协议见 BENCHMARK 第 8 节） | `datasets/v1_20261123/rag/golden_adversarial.json` |
| behavior | 行为质量：有据率、切题度、正确拒答 | LLM-as-judge（faithful/relevant/abstain） | `datasets/v1_20261123/rag/cases.jsonl` 全量按类 |

behavior 层按类别覆盖三组专项：**robust**（同一事实的五种问法：同义/错字/缩写/口语/冗余）、
**multi_turn**（同会话前置轮 + 判定最后一轮，指代消解与跨轮权限）、
**parsing**（真实 PDF/DOCX 入库后事实是否还在，夹具由 `dataset/parsing.py` 渲染并读回）。

唯一口径 = 独立数据包：预期文档按文档 id 解析、身份按 spec 登录，语料由 `seed.py`
灌入 PG/ES（跑评测前先灌库，见下）。

## Agent 调研评测

使用仓库中的合成记录可验证从工具判定到发布门禁的全过程。默认回放包括既有的网页调研边界样本和 12 个固定证据场景，分别输出报告。回放不发起模型或商品检索请求。

```bash
make evals-agent-replay
```

命令会打印报告路径。报告按用例列出工具契约、交付结果、首要故障类别及能确认的执行步骤。样本数、可判定分母、Token 覆盖与挑战集结果单列。实际调研使用 `make evals-agent`。固定证据的样本通过 `--dataset synthetic --replay` 运行。运行产物保存在 Git 忽略的 `evals/reports/`。[Agent 指标与报告对照](BENCHMARK.md#13-agent-调研评测)

### 查看工作台评测结果

评测中心要求 Agent 报告为第 3 版（`schema_version=3`）及以上，早期格式需重新运行评测生成报告。部署时应用 `023_eval_center_v3` 迁移（版本库与 RAG/Agent 双体系字段由 `022_eval_profiles_and_rag` 引入）。入库统一走 [`evals/center`](center/cli.py)；历史报告与历史数据集首次上线用回填命令补齐，重复导入相同内容与归属的批次是幂等的。

```bash
make evals-center-register                     # 登记 V1 数据集版本（幂等）
make evals-center-publish REPORT="$REPORT_PATH"
```

进入「运行分析 → 评测中心」，可按数据集版本、算法版本、口径与门禁状态筛选批次，打开批次查看逐例预期、实际工具步骤、判定维度与故障类型，并下载原始报告。对照基线要求数据集版本、口径、执行方式与判定器版本一致；真实执行只有在关联到获准查看的业务任务时才可打开轨迹中台，合成回放展示各步观测，参数只显示字段名。CLI 生成新报告时可附加 `--publish "$TENANT_ID" "$OWNER_ID"` 同步归档，门禁失败的批次也可查看。完整版本模型、接口与 Worker 执行见下文「评测中心」。

### 复现合成正例与反例

```bash
PYTHONPATH=src python -m evals.agent_eval.runner --dataset synthetic --replay evals/datasets/v1_20261123/agent/research_replay.json
PYTHONPATH=src python -m evals.agent_eval.runner --dataset synthetic --replay evals/datasets/v1_20261123/agent/research_replay.json --counterexamples evals/datasets/v1_20261123/agent/research_counterexamples.json --compare "$BASELINE_REPORT"
```

第二条命令的 `BASELINE_REPORT` 取第一条打印的报告路径。反例保留输入、版本与其余观测，只修改一个执行步骤或答案，门禁将按设计返回非零退出码。两批均归档后，在评测中心选择「合成反例复核」批次，并与「合成回放」批次对照，即可查看每个用例由通过变为失败的原因。批次详情会展示报告中可用的回放记录、反例集和模型配置的 SHA-256 摘要，供复核时确认版本。

| 工作 | 入口与输出 |
|---|---|
| 重评指定样本 | `PYTHONPATH=src python3 -m evals.agent_eval.runner --case AGENT-WEB-003 --replay evals/datasets/v1_20261123/agent/web_replay.json`。附加 `--compare <旧报告路径>` 可得到配对的修复、回归和排除项 |
| 人工盲审 | `PYTHONPATH=src python3 -m evals.agent_eval.review --replay evals/datasets/v1_20261123/agent/web_replay.json` 生成独立人工标注表。显式使用 `--model-review` 可对同一合成样本运行模型裁判。`--agreement <人工表> <模型表>` 核对有效配对与分歧 |
| 线上问题回灌 | `PYTHONPATH=src python3 -m evals.agent_eval.feedback <活动页.json> ...` 读取通过工作台权限获取的同租户活动页，按诊断代码和处理结果输出去重计数。选择候选、重建合成证据，再用真实业务入口检验修复 |

## 运行

```bash
# 前置：企业容器服务在线（make start），评估跑真实生产库；
# 独立数据包夹具已灌库（PYTHONPATH=src .venv/bin/python -m evals.dataset.seed）
make evals-quick      # 检索 + 对抗，约 5 分钟
make evals-full       # 三层全跑（180 例逐题 judge；对比基线 + 门禁，不过则退出码 1）
make evals-baseline   # 三层全跑后锁定为基线
make evals-agent      # 执行 10 个真实调研用例并保存评分报告
make evals-tests      # 评估代码自身的单元测试（不需要在线服务）
make evals-sync       # 评估集 + 最新报告 -> Phoenix（旁路；Phoenix 离线只跳过）
make evals-pipeline   # evals-full 之后自动同步；门禁不过也同步，失败样例要可见

# 只重跑某一层（迭代判定规则/修复单层时用）
PYTHONPATH=src .venv/bin/python evals/rag_eval/run.py --adversarial-only
PYTHONPATH=src .venv/bin/python evals/rag_eval/run.py --behavior-only
# 只跑某几类（冒烟/坏例定位；类别名见 cases.jsonl 的 category 字段）
PYTHONPATH=src .venv/bin/python evals/rag_eval/run.py --behavior-only --only robust,multi_turn,parsing

# 直接调用（与 make 等价）
PYTHONPATH=src .venv/bin/python evals/rag_eval/run.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`；`--compare` 与基线逐项对比、下降项报警。

## Phoenix 同步（可选旁路）

评估本身不依赖 Phoenix：`evals/rag_eval/run.py` 纯离线算分、写 `reports/*.json`。
`sync_phoenix.py` 只把已有产物搬进 Phoenix 供人查看与横向对比，**Phoenix 没起时
评估照常产出报告**——同步脚本打一句警告、退出码 0，不阻断流水线。

```bash
make evals-sync          # 数据集 + 最新报告
make evals-follow        # 跟随跑批进度逐例推（另一个终端，评估在跑时用）
make evals-pipeline      # 三层评估后自动同步

# 直接调用（Phoenix 客户端在独立 venv，项目 .venv 里没有）
PYTHONPATH=src ~/.local/share/ecom-agent/phoenix-venv/bin/python -m evals.sync_phoenix --all
PYTHONPATH=src ~/.local/share/ecom-agent/phoenix-venv/bin/python -m evals.sync_phoenix --agent "$REPORT_PATH"
```

| 入口 | 去向 | 内容 |
|---|---|---|
| `--dataset` | Phoenix **Datasets** | 180 例：input=问题/身份/多轮，output=预期行为/金标准文档/必备事实/禁披露，metadata=类别/难度/风险 |
| `--report` | Phoenix **Experiment** | 逐例 run（答案 + 引用标题/chunk_id/**引用正文**）+ evaluation（answer_pass / faithfulness_decl / answer_relevant / abstain / retrieval.*） |
| `--follow` | Phoenix **Experiment**（同一次跑、边跑边写） | 同上，只是按"每完成一例"推送而不是整份报告 |
| `--agent REPORT` | Phoenix **Experiment** | 手动导出一份当前版本的真实调研报告，保留通过、失败、不可判定三种结果 |

**实时观看**：`evals/rag_eval/run.py` 每完成一例就往 `evals/reports/live.jsonl` 追加一行
（检索层一行、行为层一行，末行 `end`）。`sync_phoenix.py --follow` 只读这个文件，
边跑边推。两段式用法：

```bash
# 终端 1：跑批（约 2 小时）
make evals-baseline
# 终端 2：跟随（评估没开始时它会等，超时 20 分钟无新数据则退出）
make evals-follow
```

跟随用的 experiment 名与 `--report` 算同一个（都用 `live.jsonl` 头部的开跑时间 +
git 短 SHA），所以跑完之后再跑 `make evals-sync` 是"已存在，跳过"，不会写第二份。
`live.jsonl` 在 `evals/reports/` 下，与本目录其他产物一样不入 Git。

幂等三条：数据集按 `metadata.case_id` 去重，只补新增；experiment 按名字
`eval-<报告时间>-<git>` 去重，已存在则跳过（`--force` 另建 `-r2`）；
evaluation 由服务端按 `(run, name)` upsert。`--dry-run` 只打印计划不写入。

环境变量：`ECOM_PHOENIX_URL`（默认 `http://127.0.0.1:6006`）、
`ECOM_PHOENIX_DATASET`（默认 `taozhi-eval-generated-v2`）。

**不同步什么**

- **门禁分数**：Phoenix 是观测层，不产出门禁分数。判定仍看 `evals/rag_eval/run.py` 的退出码
  与 `reports/*.json`，界面上的数字只供人看。
- **对抗层（18 例）**：它跑的是 `evals/datasets/v1_20261123/rag/golden_adversarial.json`，id 形如
  `Z1` / `N1` / `C1`，与评估集 `case_id`（`GEN-T-*`）实测零交集。拍平进同一个
  Experiment 只会全部落进「未匹配」——看着同步了，实际一条都没写。要让它可见，
  需单独建一个对抗数据集，暂未做。
- **在线任务轨迹**由工作台通过 OpenTelemetry 采集并写入 PostgreSQL。本节的 Phoenix 同步脚本处理评估集、实验与评估报告。[在线采集器](../src/taozhi/observability/tracing.py) · [评估同步脚本](sync_phoenix.py)

## 基线归档（只读）

`evals/baseline.json` 是"当前基线"，每次 `make evals-baseline` 都会整份覆盖；
历史值只活在 git log 里，要对比某次跑批得先翻提交。归档目录把这件事摊平：

```bash
make evals-archive         # 把当前 baseline.json 复制成 evals/baseline/<开跑时间>.json
make evals-archive-check  # 逐条比对 md5（校验在 evals/baseline/ARCHIVE.md）
```

- 一次跑批一份快照，文件名=开跑时间，与 Phoenix experiment 名
  `eval-<开跑时间>-<git>` 对齐，横向对比不用做映射；
- 快照写完即 `chmod 444` 并登记 md5：改一个字节都会被 `evals-archive-check`
  抓到。不是防人，是防"某次手改了基线却没人发现"；
- 归档**只增不改**：要新基线就跑批后追加一条，旧快照一律保留。

## 评测中心

工作台「运行分析 → 评测中心」的写入链路只有一条：`evals/center`。数据集登记、报告发布、算法版本、基线与历史回填都从它进入；`evals/agent_eval/publish.py` 与 `evals/rag_eval/publish.py` 保留为兼容旧命令的薄入口，内部委托同一实现。读取走 `/api/evaluations/*`，写入仅限老板与系统管理员，并写 `eval_audit_logs` 留痕。

### 版本模型

| 维度 | 标识 | 规则 |
| --- | --- | --- |
| 数据集版本 | `v1_20261123`（目录名即版本号） | `evals/datasets/<version>/dataset.json` 登记套件、用例数、逐文件 sha256、可用口径（`scopes`）与执行方式（`modes`）；报告内嵌 `dataset.version` 与 `content_sha256`，入库逐项校验。任何文件内容变更必须新增版本目录并升级版本号，禁止原地覆盖历史版本。 |
| 算法版本 | `algo-v1`、`algo-v2`…（`version_no` 递增） | `digest` = Git 提交 + 模型标识 + 检索参数 + Prompt 源文件 sha256 + 判定器摘要；同摘要自动复用已有版本，显式 `version_tag` 重复则拒绝；报告内嵌 `algorithm` 块优先于当前运行时快照。 |
| 批次 | 报告自带 `run_id` | `eval_runs` 同时记录 `dataset_id`/`dataset_version`、`scope`、`mode` 与 `profile_id`，形成「数据集版本 × 算法版本 × 口径 × 执行方式」四元组。 |
| 基线 | `baseline_id` | 绑定 `(eval_kind, dataset_version, scope, mode)` 的唯一 active；设置新基线时旧基线转历史保留，不清空。 |

- 口径 `scope`：`full` / `quick` / `retrieval` / `adversarial` / `behavior`；执行方式 `mode`：`live` / `synthetic_trace_regrade`。触发评测时两者的取值必须落在数据集描述符登记的 `scopes`/`modes` 内。
- 历史缺失内嵌快照的报告统一绑定当时运行时快照的 `algo-v1`，不冒充后来的算法版本。

### V0 / V1 归属

| 来源 | 归属规则 | 可比性 |
| --- | --- | --- |
| Agent 历史报告 | 报告 `dataset_sha256` 与 V1 用例目录指纹一致 → 归 `agent_research-v1_20261123`；无内嵌版本且摘要无法匹配 → 归历史归档数据集 | 只有归入 V1 的批次参与 V1 基线与趋势 |
| RAG 历史报告 | 用例与语料内容已与 V1 不同，入库时 `--legacy` 归 `<suite>-v0_legacy`，`archived=true` | `comparable=false`，不得与 V1 批次直接比较 |
| RAG 原始基线 | 保留为 `v0_legacy` 数据集版本上的 active 基线（20261117 快照），不迁入 V1 | V1 基线由 V1 批次重新锁定 |
| 旧式 Agent 数据集 | 回填时把旧数据集下的批次迁入统一 V1 数据集并归档旧数据集行 | 批次内容与报告保持原样 |

已归档数据集仍可读历史批次与原始报告，但不可再触发新评测。

### 入库与回填命令

```bash
make evals-center-register                          # 登记当前 V1 数据集（幂等，首次登记写审计）
make evals-center-publish REPORT="$REPORT_PATH"     # 发布单份报告
make evals-center-publish PUBLISH_ARGS="--kind rag --reports-dir evals/reports"   # 批量发布
make evals-center-backfill                          # 回填历史数据集、报告与基线（幂等）
make evals-center-backfill BACKFILL_ARGS="--reset"  # 先清空评测中心写入表再回填（破坏性，仅显式使用）
make evals-center-check                             # 数据集 sha / 基线唯一性 / 报告对象自检
```

- 单份报告发布成功后，最后一行输出 JSON 摘要（`run_id`、`created`、`gate_passed`、`eval_kind`、`dataset_id`、`dataset_version`、`scope`、`mode`、`report_sha256`）；Worker 解析该行回填任务结果，是必须保持的契约。
- `publish` 可显式传 `--dataset-id`、`--dataset-version`、`--scope`、`--mode`、`--profile-id`；`--baseline` 在入库成功后把该批次锁定为对应键的 active 基线；`--legacy` 按历史版本归档（内容摘要不可验证）。
- Agent 兼容旧入口：`PYTHONPATH=src python -m evals.agent_eval.publish "$REPORT_PATH" --tenant-id "$TENANT_ID" --owner-id "$OWNER_ID"`（也支持 `--reports-dir`、`--baseline`、`--profile-id`），内部委托 `evals/center`。
- RAG 入口：`PYTHONPATH=src python -m evals.rag_eval.publish --report "$REPORT_PATH"`（或 `--reports-dir`），同样委托 `evals/center`。
- 归属默认按 `ECOM_TENANT_CODE`（`mofa`）与登录名 `owner` 解析，也可显式传 `--tenant-id`/`--owner-id`。报告版本、数据集摘要或逐例计数校验失败的整份拒绝入库；重复入库返回 `created=false` 且不产生副作用。
- `make evals-publish` / `make evals-agent-publish` 保留为 Agent 旧工作流快捷方式，实际入库同样经过 `evals/center`。

### 读取与写入接口

| 方法与路径 | 用途 | 权限 |
| --- | --- | --- |
| `GET /api/evaluations/datasets` | 数据集版本目录，支持 `eval_kind`、`version`、`include_archived` | 登录账号，按可见发起人范围汇总 |
| `GET /api/evaluations/datasets/{dataset_id}` | 数据集详情与用例清单，`cursor` 偏移分页，可按 `suite` 过滤 | 同上 |
| `GET /api/evaluations/profiles` | 算法版本列表（`version_no`/`digest`/技术栈/检索参数/Prompt 快照）与批次聚合 | 登录账号 |
| `POST /api/evaluations/profiles` | 登记算法版本，同摘要复用、同名标签冲突 `409` | 老板/系统管理员 |
| `GET /api/evaluations/baselines` | active 与历史基线，支持 `eval_kind`、`dataset_version`、`include_history` | 登录账号 |
| `POST /api/evaluations/baselines` | 把批次设为 active 基线（`{run_id, note}`），旧基线转历史 | 老板/系统管理员 |
| `GET /api/evaluations/runs` | 批次列表，支持 `eval_kind`、`mode`、`scope`、`gate_passed`、`dataset_id`、`profile_id`、`algorithm_version`、`baseline_only`、`owner_id`，时间游标分页 | 登录账号，按可见发起人范围 |
| `GET /api/evaluations/runs/{run_id}` | 批次详情：主指标、原始报告可用性、报告自带基线对比 | 同上 |
| `GET /api/evaluations/runs/{run_id}/report` | 下载原始报告 JSON，响应头 `X-Report-Sha256` 供校验 | 同上，需有原始报告 |
| `GET /api/evaluations/runs/{run_id}/layers` | RAG 分层指标（检索/对抗/行为/六项工程指标）；Agent 批次返回空集 | 同上 |
| `GET /api/evaluations/runs/{run_id}/comparison` | 与指定 `baseline_id` 逐例配对比较，口径/方式/判定器不一致时 `422` | 同上 |
| `GET /api/evaluations/runs/{run_id}/cases` | 逐例判定分页，支持 `status`、`suite`、`case_id` 游标 | 同上 |
| `GET /api/evaluations/runs/{run_id}/cases/{case_id}` | 单例预期、实际步骤与判定理由；真实执行关联轨迹入口 | 同上 |
| `GET /api/evaluations/metrics/trends` | 主指标时间序列、active 基线摘要与 `pass_rate_delta`、批次聚合 | 登录账号 |
| `POST /api/evaluations/runs/trigger` | 触发真实评测，返回 `202` 与 `job_id`、`audit_id`；同租户已有排队/执行中的评测任务时 `409`（串行） | 老板/系统管理员 |
| `GET /api/evaluations/jobs/{job_id}` | 评测任务状态、阶段进度、结果与错误 | 任务发起人或老板/系统管理员 |
| `POST /api/evaluations/jobs/{job_id}/cancel` | 请求取消排队或执行中的评测任务 | 任务发起人或老板/系统管理员 |
| `GET /api/evaluations/audit-logs` | 评测写入审计（可按 `action` 过滤），时间游标分页 | 老板/系统管理员 |

读操作按租户与可见发起人范围裁剪；写操作复用老板/管理员窄写规则，并在 `eval_audit_logs` 记录 `register_dataset`、`register_profile`、`trigger_run`、`publish_run`、`set_baseline` 留痕。可比性判定要求数据集版本、`scope`、`mode` 与 `judges_sha256` 全部一致；归档数据集或缺摘要的历史批次一律标为不可比。

### Worker 执行评测任务

评测任务与资料解析、问答共用任务队列，`jobs.kind=evaluation`：

1. `POST /runs/trigger` 先校验数据集未归档、类型/口径/执行方式与描述符一致、算法版本存在，且同租户没有排队或执行中的评测任务，然后创建任务并写 `trigger_run` 审计。
2. Worker 认领后由 [`src/taozhi/application/evaluation/execution.py`](../src/taozhi/application/evaluation/execution.py) 启动运行器子进程：Agent 走 `python -m evals.agent_eval.runner --output-dir … --progress-jsonl …`（合成回放追加 `--dataset synthetic --replay …`）；RAG 按口径走 `python -m evals.rag_eval.run` 的 `--full`/`--quick`/`--retrieval-only`/`--adversarial-only`/`--behavior-only`，并带 `--report-path … --progress-jsonl …`。
3. 运行器逐阶段追加进度 JSONL，Worker 尾随写入任务阶段、百分比并同步生成 `task_events` 进度事件；取消请求会终止整个子进程组（先 `SIGTERM` 后 `SIGKILL`），超时由 `ECOM_EVAL_TIMEOUT_SECONDS`（默认 3600 秒）约束。
4. 运行结束定位报告文件，调用 `evals.center.cli publish` 完成校验、对象存储与落库；成功最后一行 JSON 回填 `job.result`，并写 `publish_run` 审计。
5. 门禁失败不等于执行失败：`gate_passed=false` 的报告照常入库、任务按 `completed` 结束；只有运行器崩溃、缺报告、超时或入库校验失败才记 `failed`，取消记 `cancelled`。

前置条件：生产 API 与 Worker 在线；RAG 评测还要求 `evals.dataset.seed` 已把语料、身份、权限与向量灌入生产库，且嵌入/检索服务可用；MinIO 不可用时告警并回退本地私有目录。运行日志在 `data/runtime/eval_jobs/<job_id>/runner.log`，进度在 `progress.jsonl`，前端通过 `GET /api/evaluations/jobs/{job_id}` 观测。

### 原始报告与存储

- PostgreSQL 存元数据与索引：数据集版本、算法版本、批次指标、逐例判定、基线、审计，以及 `eval_run_reports` 的报告对象位置与摘要。
- 原始报告 JSON 存对象存储：`ECOM_MINIO_ENABLED=true` 时写 MinIO 私有桶，否则回退本机私有目录；对象键为 `evaluations/<eval_kind>/<dataset_id>/<run_id>/report.json`，存储层再按租户加前缀隔离。
- 单份报告上限 20 MB；同一批次重复写入内容一致则跳过，不一致则拒绝覆盖；下载时重算 sha256，与登记值不符返回 `409`。
- 数据集内容（`dataset.json` 描述符、用例、语料、回放与反例）随代码进 Git；运行产物留在 `reports/`（Git 忽略）。

### 清理与重建基线

旧版本或旧批次需要整体替换时用统一清理命令，默认只预演并打印将删除的数量：

```bash
make evals-center-purge PURGE_ARGS="--mode legacy"          # 预演：归档数据集 + 其批次/基线/报告
make evals-center-purge PURGE_ARGS="--mode legacy --yes"    # 执行
make evals-center-purge PURGE_ARGS="--mode runs --yes"      # 清空全部批次/基线/算法版本，保留数据集
make evals-center-purge PURGE_ARGS="--mode all --yes"       # 连同数据集登记一起删除（破坏性）
```

清理会同时删除对象存储中的原始报告并写 `purge_data` 审计（`--include-audit` 才会连审计一起删）。重建基线流程：清理 → `make evals-center-register` 确认数据集登记 → 按口径重跑评测（受控任务或运行器 CLI）→ `publish` 入库 → 在评测中心把新批次设为 active 基线 → `make evals-center-check` 复核。

### 入库自检

`make evals-center-check` 只读校验并以退出码表达结果：逐文件重算数据集描述符 sha256、核对登记用例数与用例集、检查 active 基线键唯一、基线批次存在、`eval_run_reports` 对象可读且摘要一致、批次是否缺少原始报告。发现问题逐条打印 `FAIL`，不自动修复。

## 评测台账留痕规范（增量追加，不覆盖历史）

所有实验与评测记录文档**只允许在末尾增量追加，严禁覆盖或删改已有历史记录**，确保每次迭代的技术栈与指标完整保留、对外可追溯：

- **RAG 三层评测台账**：[`evals/rag_eval/测评记录.md`](rag_eval/测评记录.md)
- **多智能体调研台账**：[`evals/agent_eval/测评记录.md`](agent_eval/测评记录.md)
- **自动追加机制**：运行 `evals.rag_eval.run` 或 `evals.agent_eval.runner` 生成 JSON 报告后，[`evals/history_log.py`](history_log.py) 会自动以只追加（append-only）模式把当次时间、Git SHA、当前模型与检索技术栈配置及核心指标表追加到对应台账末尾（完整历史里程碑与手工 Badcase 归因均保留）。
- **每条记录必备要素**：测试元信息（时间、SHA、模式、报告路径）、技术栈与模型环境（模型版本、检索/重排/缓存配置）、改动方法、实测指标与对比增减、Badcase 归因与结论。

## 改动后的验证流程

评判标准只有这一套。任何改动——检索参数、合成链路、提示词、判定规则本身——都按
同一流程验收，不允许"这次特殊只看某几条"；改什么跑什么的触发矩阵见 BENCHMARK F2。

1. `make evals-tests`：评估代码自身单测（判定器契约、数据集契约、身份同源、
   AST 回归断言），不需要在线服务，秒级。CI 也跑这一层。
2. `make evals-full`：三层全跑——与 `baseline.json` 做**配对 bootstrap**（只有显著下降
   才算回归）、按硬底线门禁校验（未达标退出码 1，供 CI 阻塞合并；没跑的层跳过）。
   判定语义见 BENCHMARK 第 12 节。需要生产服务在线，本机跑，不进 CI。
3. 读结果：无回归且门禁过 → 改动可提交；否则修到通过；只有确属有意变更
   （提交信息写明原因）才走第 4 步。
4. `make evals-baseline`：新结果锁定为基线，随提交进 Git。刷新基线 = 宣布新水位，
   提交信息必须写明修了什么、哪个指标为什么变、旧水位多少。

## 指标口径

口径以 **[`BENCHMARK.md`](BENCHMARK.md) 为唯一来源**：模块边界、指标定义、判定协议、
报告 schema、数据契约、确定性设计、变更规则都在那里。本文件只讲"怎么跑"。

`context_precision@8` 不设门禁：32 篇小语料上分母有结构性上限，理由见 BENCHMARK 7.1。

`answer_relevancy` / `context_recall@8` 从硬底线降为**目标线**（只提示不阻塞）：本项目
口径与业界同名指标不同源，跨口径套阈值会让门禁永远红，见 BENCHMARK 12.1。

judge 分数要作门禁依据，先过校准（`evals/calibrate.py`，kappa ≥ 0.6），见 BENCHMARK 12.3。

## 新增用例

调研合成用例由已版本化的工具证据、参考事实和定向反例组成。轨迹访问权限单独通过带登录身份的 HTTP 接口验证。[Agent 数据集设计与验收](agent_eval/数据集设计.md)

RAG 用例进入独立数据包：改 spec → `generate` → `seed` → 跑评测。字段、金丝雀、判定规则、
权限夹具的约束见 [`dataset/README.md`](dataset/README.md)（数据包说明书）与
BENCHMARK 第 10 节（数据契约），不在本文件复述。
