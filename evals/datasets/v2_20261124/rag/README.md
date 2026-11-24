# V2 企业知识问答（RAG）三层评测数据集（`v2_20261124/rag`）

本目录存放 **企业知识文档问答与受控问数（RAG + ChatBI）** 的 V2 基准数据集，按 **检索层、安全对抗层、行为质量层** 划分，分类口径使用业界标准全称。

## 文件清单

| 文件名 | 规模 | 说明 |
|---|---|---|
| `cases.jsonl` | 200 例（27 类 / 9 大领域角色） | 覆盖商品规格、兼容性、售后、运营、供应链、财务、计算、引用、冲突、时效、长上下文、多文档、多轮、解析与 ChatBI |
| `corpus_docs.jsonl` | 56 篇文档 | 与 `cases.jsonl` 配套的企业语料与多版本/多密级/跨部门权限夹具（含 14 篇 PDF/DOCX 解析夹具） |
| `manifest.json` | 元数据清单 | 记录生成参数、`seed`、spec 摘要与语料/用例统计 |
| `golden_adversarial.json` | 30 例 | 全自动规则判定的安全对抗集（提示词注入、越权、撤权、金丝雀防泄漏、拒答边界） |

## 用例分类（27 类标准名）

- **业务场景**：`product`、`compatibility`、`after_sales`、`operations`、`supply_chain`、`finance`、`chatbi`
- **质量与行为**：`calculation`、`citation`、`sales_coaching`、`conflict`、`edge_case`、`exclusion`、`insufficient_evidence`、`long_context`、`multi_document`、`multi_turn`、`parsing`、`robustness`、`timeliness`、`troubleshooting`、`ambiguity`
- **安全治理**：`role_based_access`、`access_grant`、`revocation`、`prompt_injection`、`data_leakage`

## 五维标注分布

| 维度 | 分布 |
|---|---|
| answerability | answerable 148 / partial 16 / unanswerable 36 |
| qtype | fact 96 / security 32 / edge_case 16 / multi_hop 16 / comparison 14 / troubleshooting 9 / process 8 / ambiguity 5 / aggregation 2 / false_premise 2 |
| difficulty | easy 47 / medium 91 / hard 62 |
| source | synthetic 172 / adversarial 15 / badcase 7 / real 6 |
| risk | low 109 / high 46 / medium 45 |

## 常用执行入口

```bash
make evals-quick
make evals-full
```

用例由 `evals/dataset/spec/` 确定性生成（见 `evals/dataset/README.md`），改 spec 后重跑 `python -m evals.dataset.generate`。

## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 split |
| Anthropic, Bloom | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, AI-resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |
| RAGAS / ARES / RAGBench / CRAG / RGB / LongMemEval | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | 子类口径参考（v3 起正式对齐） | RAG |

## 命名口径说明

v2 使用业务/能力混合分类；v3 起改为"6 大类 capability + 官方术语子类 subcategory + 业务主题 topic"，
完整映射见 [`v3_20261124/rag/README.md`](../../v3_20261124/rag/README.md)。
v2 数据保持只读，不回溯重命名。
