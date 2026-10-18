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
│   ├── cases.jsonl              200 例历史集（--dataset legacy 口径，语料在磁盘 data/）
│   ├── 评测说明.md               数据集说明书：字段口径、类别分布、关键规则
│   ├── golden_questions.jsonl   40 例含参考答案的问答集（用于答案质量判定）
│   ├── golden_adversarial.json  18 例对抗集（注入/越权/泄漏/冲突，每条带机读 judge
│   │                             规则 + basis 语料溯源，两口径共用，全自动判定）
│   └── generated/v1/            独立包生成产物：44 例 + 16 篇语料 + manifest（latest/ 为指针）
├── metrics.py                   指标实现（全项目唯一口径：Hit@k / P@k / R@k / nDCG@k / MRR / pass^k）
├── run_evals.py                 三层评估运行器（检索 · 对抗 · 行为；--dataset 切换口径）
├── e2e_ask_data.py              问数链路端到端验收
├── verify_api_end_to_end.py     API 端到端回归（登录→提问→轮询）
├── experiments/                 一次性实证实验（结论已回写代码注释）
├── legacy/                      已被 run_evals.py 取代的早期原型，仅作历史留存，不维护
├── reports/                     运行产物（Git 忽略；legacy/ 内保留历史报告）
├── baseline.json                legacy 口径基线快照，随代码进 Git
├── baseline.generated.json      generated 口径基线快照，随代码进 Git
└── tests/                       评估代码自身的单元测试
```

## 三层评估

| 层 | 评什么 | 判定方式 | 数据来源 |
|---|---|---|---|
| retrieval | 检索质量：Hit@1/3/8、Precision@k、Recall@k、nDCG@k、MRR、延迟 | 规则判定（命中预期标题集合） | `--dataset` 指定口径抽样 |
| adversarial | 安全：注入、越权、撤权、金丝雀泄漏 | 全自动规则判定（机读 judge 规则，零人工复核） | `datasets/golden_adversarial.json` |
| behavior | 行为质量：有据率、切题度、正确拒答 | LLM-as-judge（faithful/relevant/abstain） | `--dataset` 指定口径分类抽样 |

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

`--dataset legacy`（默认）：200 例历史集，预期文档按 `source_files` 路径解析；
`--dataset generated`：独立数据包，预期文档按文档 id 解析、身份按 spec 登录
（需先 `python -m evals.dataset.seed` 灌库）。

## 运行

```bash
# 前置：企业服务在线（bash scripts/start_services.sh），评估跑真实生产库
make evals-quick      # 检索 + 对抗，约 5 分钟
make evals-full       # 三层全跑，约 40 分钟
make evals-baseline   # 结果锁定为基线
make evals-tests      # 评估代码自身的单元测试（不需要在线服务）

# 只重跑某一层（迭代判定规则/修复单层时用）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --adversarial-only
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --behavior-only --dataset generated

# 独立数据包口径（先灌库夹具，再跑；行为层按 spec 身份登录）
PYTHONPATH=src ECOM_ENV_FILE="$PWD/.env.production" .venv/bin/python -m evals.dataset.seed
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --dataset generated --compare

# 或直接调用（legacy 口径）
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`，`--compare` 会与对应口径的基线逐项对比并对下降项报警。

## 基线与门禁按口径分流

两个口径的语料、身份、预期解析都不同，基线与门禁各自独立，不混用：

- `--dataset legacy` → `evals/baseline.json`，门禁 `GATE_THRESHOLDS`（含 context_precision@8 ≥ 0.75）；
- `--dataset generated` → `evals/baseline.generated.json`，门禁 `GATE_THRESHOLDS_GENERATED`。
  generated 口径不设 precision@8 门禁：16 篇迷你语料上该指标有结构性上限——单预期文档的
  用例最高 1/8=0.125、双预期 0.25，实测 0.36 已接近完美系统的表现；对它设阈值衡量的是
  语料结构而非系统质量。回归检测由 recall@8 / hit@8 / MRR 与 `--compare` 逐项对比承担。
  faithfulness/relevancy 与语料规模无关，两个口径同阈值。

`--gate` 按当前口径的门禁校验，未达标退出码 1（供 CI 阻塞合并）；没跑的层是 None，校验时跳过。

## 指标口径（改动前先看这里）

- **相关性是二值的**：候选标题命中预期标题集合即为相关。不做模糊匹配——宁可低估召回，也不要虚高。
- **Hit@k** 只回答"前 k 条里有没有命中"，不反映排序好坏；**nDCG@k 才惩罚把相关项埋在后面**。两个一起看。
- **所有均值都给 95% 自举置信区间**（`metrics.summarize`）。样本量小时点估计没有决策价值。
- **pass^k**：非确定性系统看"k 次全过"，单次通过率会系统性高估稳定性。端到端验收必须两者都报。
- 指标在计算层不取整，取整只在渲染层，避免基线对比出现假的"无变化"。

## 改阈值之前

检索阈值（`ECOM_ANN_MAX_DIST`、`GATE_MARGIN`、精排双阈值、`ECOM_MAX_CHUNKS_PER_DOC`）都属于**待实证参数**。
调整流程：先跑 `make evals-quick` 拿当前基线 → 改参数 → 重跑 → 对比再看是否真的改善；不要凭单条 query 的手感改。

## 新增用例规范

**新用例一律进独立数据包**（`evals/dataset/`，见其 README）：改 spec → `generate` → `seed` →
`run_evals.py --dataset generated`。权限夹具随包分发，换机可重建；`--verify` 的权限实测矩阵
保证夹具状态与 spec 一致。历史集 `datasets/cases.jsonl` 只维护不新增。

两条口径都适用的原则：

1. 字段遵循各自说明书的口径（`case_id`/`category`/`question`/`expected_behavior`/`pass_criteria` 必填）。
2. 答案只能从实际交付资料推导；资料标"未知"的题，预期行为是如实说明资料不足，**不设编造的标准答案**。
3. 权限类用例必须绑定真实夹具状态，不得通过问题正文指定角色绕过认证。
4. 机密标记（`SYN-CANARY-*`）只允许出现在 `forbidden_disclosure` 与 `evidence_location`。
5. 夹具缺失的用例不许入库：schema 会挡（预期文档/身份/金丝雀必须可解析），
   跑的时候覆盖率告警会挡（解析不出预期的例不进指标，静默小子集统计比没有指标更危险）。
6. 对抗用例的判定规则必须可机读（`judge`）且挂在语料事实上（`basis`）——评测链路不留人工复核口子。
