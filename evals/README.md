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

# 只重跑某一层（迭代判定规则/修复单层时用）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --adversarial-only
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only
# 只跑某几类（冒烟/坏例定位；类别名见 cases.jsonl 的 category 字段）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only --only robust,multi_turn,parsing

# 直接调用（与 make 等价）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`；`--compare` 与基线逐项对比、下降项报警。

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
