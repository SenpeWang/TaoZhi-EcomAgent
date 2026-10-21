# evals/ — 评估体系（数据集 · 指标 · 运行器）

本目录是项目**唯一**的评估资产存放处。数据集、评估代码、基线、报告全部在这里；
`scripts/` 只放部署与运维脚本，不出现任何评估逻辑。

## 目录结构

```
evals/
├── dataset/                     独立评测数据包：规格 → 确定性生成 → 幂等灌库（见其 README）
├── datasets/                    数据集（随代码进 Git，可追溯、可 diff）
│   ├── golden_adversarial.json  18 例对抗集（注入/越权/泄漏/冲突，每条带机读 judge
│   │                             规则 + basis 语料溯源，全自动判定）
│   └── generated/latest/         独立包生成产物：108 例 + 32 篇语料（含 10 篇 PDF/DOCX 解析夹具）
├── metrics.py                   指标实现（全项目唯一口径）
├── run_evals.py                 三层评估运行器（检索 · 对抗 · 行为），--only 可按类别过滤
├── BENCHMARK.md                 口径唯一来源：指标定义·判定协议·报告 schema·数据契约
├── reports/                     运行产物（Git 忽略）
├── baseline.json                唯一基线快照，随代码进 Git
└── tests/                       评估代码自身的单元测试
```

## 三层评估

| 层 | 评什么 | 判定方式 | 数据来源 |
|---|---|---|---|
| retrieval | 检索质量：Hit@1/3/8、Precision@k、Recall@k、nDCG@k、MRR、延迟 | 规则判定（命中预期标题集合） | `datasets/generated/latest/` 全量 |
| adversarial | 安全：注入、越权、撤权、金丝雀泄漏 | 全自动规则判定（机读规则，零人工复核；协议见 BENCHMARK 第 8 节） | `datasets/golden_adversarial.json` |
| behavior | 行为质量：有据率、切题度、正确拒答 | LLM-as-judge（faithful/relevant/abstain） | `datasets/generated/latest/` 全量按类 |

behavior 层按类别覆盖三组专项：**robust**（同一事实的五种问法：同义/错字/缩写/口语/冗余）、
**multi_turn**（同会话前置轮 + 判定最后一轮，指代消解与跨轮权限）、
**parsing**（真实 PDF/DOCX 入库后事实是否还在，夹具由 `dataset/parsing.py` 渲染并读回）。

唯一口径 = 独立数据包：预期文档按文档 id 解析、身份按 spec 登录，语料由 `seed.py`
灌入 PG/ES（跑评测前先灌库，见下）。

## 运行

```bash
# 前置：企业服务在线（bash scripts/start_services.sh），评估跑真实生产库；
# 独立数据包夹具已灌库（PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed）
make evals-quick      # 检索 + 对抗，约 5 分钟
make evals-full       # 三层全跑，约 3 小时（108 例逐题 judge；对比基线 + 门禁，不过则退出码 1）
make evals-baseline   # 三层全跑后锁定为基线
make evals-tests      # 评估代码自身的单元测试（不需要在线服务）
make evals-sync       # 评估集 + 最新报告 -> Phoenix（旁路；Phoenix 离线只跳过）
make evals-pipeline   # evals-full 之后自动同步；门禁不过也同步，失败样例要可见

# 只重跑某一层（迭代判定规则/修复单层时用）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --adversarial-only
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only
# 只跑某几类（冒烟/坏例定位；类别名见 cases.jsonl 的 category 字段）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only --only robust,multi_turn,parsing

# 直接调用（与 make 等价）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`；`--compare` 与基线逐项对比、下降项报警。

## Phoenix 同步（可选旁路）

评估本身不依赖 Phoenix：`run_evals.py` 纯离线算分、写 `reports/*.json`。
`sync_phoenix.py` 只把已有产物搬进 Phoenix 供人查看与横向对比，**Phoenix 没起时
评估照常产出报告**——同步脚本打一句警告、退出码 0，不阻断流水线。

```bash
make evals-sync          # 数据集 + 最新报告
make evals-follow        # 跟随跑批进度逐例推（另一个终端，评估在跑时用）
make evals-pipeline      # 三层评估后自动同步

# 直接调用（Phoenix 客户端在独立 venv，项目 .venv 里没有）
ECOM_PHOENIX_URL=http://127.0.0.1:6006 \
  ~/.local/share/ecom-agent/phoenix-venv/bin/python evals/sync_phoenix.py --all
```

| 入口 | 去向 | 内容 |
|---|---|---|
| `--dataset` | Phoenix **Datasets** | 108 例：input=问题/身份/多轮，output=预期行为/金标准文档/必备事实/禁披露，metadata=类别/难度/风险 |
| `--report` | Phoenix **Experiment** | 逐例 run（答案 + 引用标题/chunk_id/**引用正文**）+ evaluation（answer_pass / faithfulness_decl / answer_relevant / abstain / retrieval.*） |
| `--follow` | Phoenix **Experiment**（同一次跑、边跑边写） | 同上，只是按"每完成一例"推送而不是整份报告 |

**实时观看**：`run_evals.py` 每完成一例就往 `evals/reports/live.jsonl` 追加一行
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

- **门禁分数**：Phoenix 是观测层，不产出门禁分数。判定仍看 `run_evals.py` 的退出码
  与 `reports/*.json`，界面上的数字只供人看。
- **对抗层（18 例）**：它跑的是 `evals/datasets/golden_adversarial.json`，id 形如
  `Z1` / `N1` / `C1`，与评估集 `case_id`（`GEN-T-*`）实测零交集。拍平进同一个
  Experiment 只会全部落进「未匹配」——看着同步了，实际一条都没写。要让它可见，
  需单独建一个对抗数据集，暂未做。
- **Traces**：项目代码没有 OTel 埋点，Traces 页不会自动出现线上调用链，
  需要另行接 instrumentor。

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

一律进独立数据包：改 spec → `generate` → `seed` → 跑评测。字段、金丝雀、判定规则、
权限夹具的约束见 [`dataset/README.md`](dataset/README.md)（数据包说明书）与
BENCHMARK 第 10 节（数据契约），不在本文件复述。
