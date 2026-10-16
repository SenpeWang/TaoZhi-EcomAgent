# evals/ — 评估体系（数据集 · 指标 · 运行器）

本目录是项目**唯一**的评估资产存放处。数据集、评估代码、基线、报告全部在这里；
`scripts/` 只放部署与运维脚本，不出现任何评估逻辑。

## 目录结构

```
evals/
├── datasets/                    数据集（随代码进 Git，可追溯、可 diff）
│   ├── cases.jsonl              200 例行为/权限评测集（正式版，合并自 part1+part2）
│   ├── 评测说明.md               数据集说明书：字段口径、类别分布、关键规则
│   ├── golden_questions.jsonl   40 例含参考答案的问答集（用于答案质量判定）
│   └── golden_adversarial.json  18 例对抗集（注入/越权/泄漏/冲突）
├── metrics.py                   指标实现（全项目唯一口径：Hit@k / P@k / R@k / nDCG@k / MRR / pass^k）
├── run_evals.py                 三层评估运行器（检索 · 对抗 · 行为）
├── e2e_ask_data.py              问数链路端到端验收
├── verify_api_end_to_end.py     API 端到端回归（登录→提问→轮询）
├── experiments/                 一次性实证实验（结论已回写代码注释）
├── legacy/                      已被 run_evals.py 取代的早期原型，仅作历史留存，不维护
├── reports/                     运行产物（Git 忽略；legacy/ 内保留历史报告）
├── baseline.json                基线快照，随代码进 Git
└── tests/                       评估代码自身的单元测试
```

## 三层评估

| 层 | 评什么 | 判定方式 | 数据来源 |
|---|---|---|---|
| retrieval | 检索质量：Hit@1/3/8、Precision@k、Recall@k、nDCG@k、MRR、延迟 | 规则判定（命中预期标题集合） | `datasets/cases.jsonl` 抽样 |
| adversarial | 安全：注入、越权、撤权、金丝雀泄漏 | 规则 + 人工复核 | `datasets/golden_adversarial.json` |
| behavior | 行为质量：有据率、切题度、正确拒答 | LLM-as-judge（faithful/relevant/abstain） | `datasets/cases.jsonl` 分类抽样 |

## 运行

```bash
# 前置：企业服务在线（bash scripts/start_services.sh），评估跑真实生产库
make evals-quick      # 检索 + 对抗，约 5 分钟
make evals-full       # 三层全跑，约 40 分钟
make evals-baseline   # 结果锁定为基线
make evals-tests      # 评估代码自身的单元测试（不需要在线服务）

# 或直接调用
ECOM_ENV_FILE="$PWD/.env.production" PYTHONPATH=src .venv/bin/python evals/run_evals.py --quick --compare
```

结果落盘 `evals/reports/<时间戳>-<git短SHA>.json`，`--compare` 会与 `baseline.json` 逐项对比并对下降项报警。

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

1. 加进 `datasets/cases.jsonl`，字段遵循 `评测说明.md` 的口径（`case_id`/`category`/`question`/`source_files`/`expected_behavior`/`pass_criteria` 必填）。
2. 答案只能从实际交付资料推导；资料标"未知"的题，预期行为是如实说明资料不足，**不设编造的标准答案**。
3. 权限类用例必须绑定真实夹具状态，不得通过问题正文指定角色绕过认证。
4. 机密标记（`SYN-CANARY-*`）只允许出现在 `forbidden_disclosure` 与 `evidence_location`。
