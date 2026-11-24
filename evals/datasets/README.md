# 评测数据集（按体系 + 版本存储）

本目录存放所有评测数据集快照，**RAG 与 Agent 分开、版本名自带体系前缀**：

```
evals/datasets/
├── README.md                  # 本文件：体系 × 版本矩阵与口径入口
├── REVIEW.md                  # 当前 v3 核对清单
├── rag_v1_20261123/           # RAG 历史只读
├── agent_v1_20261123/         # Agent 历史只读
├── rag_v2_20261124/           # RAG 历史只读
├── agent_v2_20261124/         # Agent 历史只读
├── rag_v3_20261124/           # RAG 当前版本
└── agent_v3_20261124/         # Agent 当前版本
```

## 版本矩阵

| 版本 | RAG 数据集 | Agent 数据集 | 状态 |
|---|---|---|---|
| `v1_20261123` | `rag_v1_20261123/`（180 主集 + 18 对抗） | `agent_v1_20261123/`（12 合成回放） | 历史只读 |
| `v2_20261124` | `rag_v2_20261124/`（200 主集 + 30 对抗） | `agent_v2_20261124/`（190 合成回放） | 历史只读 |
| `v3_20261124` | `rag_v3_20261124/`（540 主集 + 60 红队） | `agent_v3_20261124/`（600 合成回放 + 10 实测） | **当前 active** |

## 每个版本目录

- `dataset.json`：本体系套件、用例数、逐文件 sha256、可用口径与方式；
- `README.md`：数据说明、参考来源、子类命名登记表；
- RAG：`cases.jsonl`、`corpus_docs.jsonl`、`redteam.json`（v1/v2 为 `golden_adversarial.json`）、`manifest.json`；
- Agent：`research_cases.json`、`research_replay.json`、`research_counterexamples.json`、`web_replay.json`；
- Agent 另有 10 例 live 用例在 `evals/agent_eval/cases.py`，故 V3 套件 `case_count=610`。

## 设计口径

- 大类按能力划分，不按业务主题；业务主题放 `topic` 维度。
- 子类英文名对齐官方来源术语，唯一登记表见 [`../naming.py`](../naming.py)。
- 用例角色体现在 `track` 字段；`heldout` 不进入日常报告。
- 指标：`pass@k` 与 `pass^k` 同时报告；子类 n<30 不单独下结论。
- 展开细节见 `REVIEW.md`、各版本 README 与 [`../BENCHMARK.md`](../BENCHMARK.md)。

## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 track |
| Anthropic, Bloom | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, AI-resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |

> 全部为合成数据：不做人工标注、不采集真实日志。确定性 grader 参与门禁；
> LLM judge 仅作旁路观察，未经人工校准，不阻塞发布。
