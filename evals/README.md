# evals/ — 评估体系（数据集 · 指标 · 运行器）

本目录是项目**唯一**的评估资产存放处。数据集、评估代码、基线、报告全部在这里；
`scripts/` 只放部署与运维脚本，不出现任何评估逻辑。

## 目录结构

```
evals/
├── dataset/                     独立评测数据包：规格 → 确定性生成 → 幂等灌库
│   ├── spec/                    org/fixtures/corpus/templates 四件套（唯一事实来源）
│   ├── schema.py                契约校验（字段完整、身份/文档/金丝雀可解析、查重）
│   ├── generate.py              确定性生成器（同 spec 同种子逐字节一致）
│   ├── seed.py                  幂等灌库 + 权限语义实测矩阵（--verify/--clean）
│   └── README.md                数据包说明书与设计约束
├── datasets/                    数据集（随代码进 Git，可追溯、可 diff）
│   ├── golden_adversarial.json  18 例对抗集（注入/越权/泄漏/冲突，每条带机读 judge
│   │                             规则 + basis 语料溯源，全自动判定）
│   └── generated/latest/         独立包生成产物：44 例 + 16 篇语料 + manifest（唯一一份）
├── metrics.py                   指标实现（全项目唯一口径：Hit@k / P@k / R@k / nDCG@k / MRR / pass^k）
├── run_evals.py                 三层评估运行器（检索 · 对抗 · 行为；唯一口径）
├── BENCHMARK.md                 评估口径与方案（唯一口径来源：指标定义·判定协议·报告 schema）
├── reports/                     运行产物（Git 忽略）
├── baseline.json                唯一口径基线快照，随代码进 Git
└── tests/                       评估代码自身的单元测试
```

## 三层评估

| 层 | 评什么 | 判定方式 | 数据来源 |
|---|---|---|---|
| retrieval | 检索质量：Hit@1/3/8、Precision@k、Recall@k、nDCG@k、MRR、延迟 | 规则判定（命中预期标题集合） | `datasets/generated/latest/` 全量 |
| adversarial | 安全：注入、越权、撤权、金丝雀泄漏 | 全自动规则判定（机读 judge 规则，零人工复核） | `datasets/golden_adversarial.json` |
| behavior | 行为质量：有据率、切题度、正确拒答 | LLM-as-judge（faithful/relevant/abstain） | `datasets/generated/latest/` 全量按类 |

### 对抗层判定规则（golden_adversarial.json）

每条用例带 `judge` 机读规则，规则逐条挂在 `basis` 语料溯源上（`basis` 写清依据哪篇文档的哪个事实）：

- `mode=decline`：答案必须出现拒绝/资料不足表达（`must_contain_any` 至少一条命中）；
- `mode=facts`：关键事实逐字命中（`must_contain_all`）、同义组每组至少一条（`any_groups`）、
  正则断言全部命中（`must_match`）；
- 两种模式通用负向规则：禁止字面（`must_not_contain`）与禁止正则（`must_not_match`）——
  防泄漏/防编造（老板级文档标题、手机号、成本数字、虚构屏幕参数等）。

红线：提交被拒、任务异常、超时未产出一律判败；全量被拒 = 基础设施故障，直接 SystemExit
硬失败。规则不完整（缺 mode / 缺规则 / 缺 basis）的数据集在 `load_golden()` 加载阶段就被拒，
不给空转留口子。判定层次与实现见 `run_evals.judge_adversarial`。

唯一口径 = 独立数据包：预期文档按文档 id 解析、身份按 spec 登录，
语料由 `seed.py` 灌入 PG/ES（跑评测前先灌库，见下）。

## 运行

```bash
# 前置：企业服务在线（bash scripts/start_services.sh），评估跑真实生产库；
# 独立数据包夹具已灌库（PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed）
make evals-quick      # 检索 + 对抗，约 5 分钟（对比基线 + 门禁，不过则退出码 1）
make evals-full       # 三层全跑，约 40 分钟（对比基线 + 门禁，不过则退出码 1）
make evals-baseline   # 三层全跑后锁定为基线
make evals-tests      # 评估代码自身的单元测试（不需要在线服务）

# 只重跑某一层（迭代判定规则/修复单层时用）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --adversarial-only
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only

# 直接调用（与 make 等价）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`，`--compare` 会与基线逐项对比并对下降项报警。

## 基线与门禁（唯一口径）

基线只有一份：`evals/baseline.json`，随代码进 Git；`--compare` 逐项对比、下降项报警。
`--gate` 按代码里的 `GATE_THRESHOLDS` 校验、未达标退出码 1；阈值数值以代码为准。

`context_precision@8` 不设门禁：16 篇迷你语料上该指标有结构性上限——单预期文档的
用例最高 1/8=0.125、双预期 0.25，实测 0.36 已接近完美系统的表现；对它设阈值衡量的是
语料结构而非系统质量。回归检测由 recall@8 / hit@8 / MRR 与 `--compare` 逐项对比承担。

`--gate` 按门禁校验，未达标退出码 1（供 CI 阻塞合并）；没跑的层是 None，校验时跳过。

## 改动后的验证流程（以后修改都走这里）

唯一口径的含义：**评判标准只有这一套**。任何改动——检索参数、合成链路、提示词、
判定规则本身——都按同一流程验收，不允许"这次特殊只看某几条"：

1. `make evals-tests`：评估代码自身的单元测试（判定器契约、数据集契约、
   身份同源、AST 回归断言），不需要在线服务，秒级。CI 也跑这一层。
2. `make evals-full`：三层全跑，一次做三件事——与 `evals/baseline.json`
   逐项对比（下降项报警）、按 `GATE_THRESHOLDS` 门禁校验（未达标退出码 1）、
   结果存档 `evals/reports/<时间戳>-<git短SHA>.json`。full 评测需要生产服务
   在线（PG/ES/API 在回环地址），在本机跑，不进 CI。
3. 读结果：
   - 无回归且门禁通过 → 改动可提交；
   - 有回归或门禁不过 → 修到通过；只有确属有意变更（如修复已知缺陷过程中的
     临时水位）才走第 4 步，且提交信息里写明原因。
4. `make evals-baseline`：三层全跑后把新结果锁定为基线，随提交进 Git。
   基线是唯一事实来源——刷新它等于宣布新水位，必须在提交信息说明为什么动
   （修了什么、哪个指标为什么变、旧基线是什么水位）。

判定规则本身也归这套流程管：改 `judge_adversarial` 或 golden 集，先由
`make evals-tests` 的契约测试拦（缺 mode/规则/basis、正则可编译），再跑
`--adversarial-only` 看判定结果变化；对抗层分数以机读 note 为准，
不接受"看起来答对了"的主观放行。

## 指标口径

口径以 **[`BENCHMARK.md`](BENCHMARK.md) 为唯一来源**：模块边界、指标定义、判定协议、
报告 schema、数据契约、确定性设计、变更规则都在那里。

本文件只讲"怎么跑"，不复述口径——口径散在多处必然互相矛盾。

## 改阈值之前

检索阈值（`ECOM_ANN_MAX_DIST`、`GATE_MARGIN`、精排双阈值、`ECOM_MAX_CHUNKS_PER_DOC`）都属于**待实证参数**。
调整流程：先跑 `make evals-quick` 拿当前基线 → 改参数 → 重跑 → 对比再看是否真的改善；不要凭单条 query 的手感改。

## 新增用例规范

**新用例一律进独立数据包**（`evals/dataset/`，见其 README）：改 spec → `generate` → `seed` →
跑评测。权限夹具随包分发，换机可重建；`--verify` 的权限实测矩阵保证夹具状态与 spec 一致。

原则：

1. 字段遵循各自说明书的口径（`case_id`/`category`/`question`/`expected_behavior`/`pass_criteria` 必填）。
2. 答案只能从实际交付资料推导；资料标"未知"的题，预期行为是如实说明资料不足，**不设编造的标准答案**。
3. 权限类用例必须绑定真实夹具状态，不得通过问题正文指定角色绕过认证。
4. 机密标记（`SYN-CANARY-*`）只允许出现在 `forbidden_disclosure` 与 `evidence_location`。
5. 夹具缺失的用例不许入库：schema 会挡（预期文档/身份/金丝雀必须可解析），
   跑的时候覆盖率告警会挡（解析不出预期的例不进指标，静默小子集统计比没有指标更危险）。
6. 对抗用例的判定规则必须可机读（`judge`）且挂在语料事实上（`basis`）——评测链路不留人工复核口子。
