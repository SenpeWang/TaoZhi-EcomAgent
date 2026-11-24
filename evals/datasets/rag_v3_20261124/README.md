# V3 RAG 评测数据集（`v3_20261124/rag`）

企业知识文档问答与受控问数（RAG + ChatBI）全合成评测集。6 个能力大类 × 6 种用途，约 600 例。

## 文件清单

| 文件 | 内容 |
|---|---|
| `cases.jsonl` | 主集约 540 例（6 大类 × 90） |
| `redteam.json` | 独立红队 60 例（规则判定） |
| `corpus_docs.jsonl` | 语料与权限夹具 |
| `manifest.json` | 生成参数、分类/用途统计、文件 sha256 |

## 字段

`case_id` / `capability`（大类）/ `category`（子类）/ `topic`（业务主题）/ `track`（用途）/
`question` / `turns` / `expected_documents` / `expected_facts` / `reference_answer` /
`pass_criteria` / 五维标注（answerability、qtype、difficulty、source、risk）。

## 规模与用途

| 用途 track | 每大类 | 说明 |
|---|---:|---|
| golden | 30 | 冻结参考集，参考解可复算 |
| capability | 20 | 能力提升集，允许低通过率 |
| regression | 15 | 回归集，近 100% 通过 |
| challenge | 10 | 挑战集，单列 |
| heldout | 15 | 留出集，发布前复核 |
| redteam | 60（独立） | 注入/越权/泄漏/拒答，硬红线 |

## 子类命名登记表

| 大类 capability | 子类 subcategory | 官方术语来源 | 状态 | v2 旧名 |
|---|---|---|---|---|
| `retrieval` | `context_recall` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | product/compatibility 检索部分 |
| `retrieval` | `context_precision` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | 检索排序 |
| `retrieval` | `context_relevance` | [ARES: An Automated Evaluation Framework for RAG (NAACL 2024)](https://aclanthology.org/2024.naacl-long.20/) | adopted | 检索相关性 |
| `retrieval` | `noise_robustness` | [RGB: Benchmarking Large Language Models in RAG (AAAI 2024)](https://arxiv.org/abs/2309.01431) | adopted | robustness |
| `retrieval` | `information_integration` | [RGB: Benchmarking Large Language Models in RAG (AAAI 2024)](https://arxiv.org/abs/2309.01431) | adopted | multi_document 合并 |
| `generation_quality` | `faithfulness` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | 有据率 |
| `generation_quality` | `response_relevancy` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | answer_relevancy |
| `generation_quality` | `factual_correctness` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | calculation/after_sales |
| `generation_quality` | `adherence` | [RAGBench / TRACe](https://arxiv.org/abs/2407.11005) | adopted | citation 支撑 |
| `generation_quality` | `completeness` | [RAGBench / TRACe](https://arxiv.org/abs/2407.11005) | adopted | 覆盖度 |
| `generation_quality` | `utilization` | [RAGBench / TRACe](https://arxiv.org/abs/2407.11005) | adopted | 召回未用 |
| `abstention_and_boundary` | `negative_rejection` | [RGB: Benchmarking Large Language Models in RAG (AAAI 2024)](https://arxiv.org/abs/2309.01431) | adopted | insufficient_evidence |
| `abstention_and_boundary` | `counterfactual_robustness` | [RGB: Benchmarking Large Language Models in RAG (AAAI 2024)](https://arxiv.org/abs/2309.01431) | adopted | conflict/edge |
| `abstention_and_boundary` | `false_premise` | [CRAG: Comprehensive RAG Benchmark](https://arxiv.org/abs/2406.04744) | adopted | conflict/edge |
| `abstention_and_boundary` | `abstention` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | 资料不足拒答 |
| `abstention_and_boundary` | `knowledge_conflict` | [Knowledge Conflicts for LLMs: A Survey](https://arxiv.org/abs/2403.08319) | adapted | conflict |
| `abstention_and_boundary` | `out_of_scope` | [本项目判定器与 BENCHMARK 口径](../../../BENCHMARK.md) | project_defined | exclusion |
| `multi_turn_and_long_context` | `multi_turn` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | multi_turn |
| `multi_turn_and_long_context` | `multi_session_reasoning` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | multi_turn 跨会话 |
| `multi_turn_and_long_context` | `temporal_reasoning` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | timeliness |
| `multi_turn_and_long_context` | `knowledge_updates` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | 政策版本更新 |
| `multi_turn_and_long_context` | `information_extraction` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | long_context |
| `security_and_authorization` | `prompt_injection` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | injection |
| `security_and_authorization` | `sensitive_information_disclosure` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | leakage |
| `security_and_authorization` | `system_prompt_leakage` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | 新增 |
| `security_and_authorization` | `excessive_agency` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | 越权动作 |
| `security_and_authorization` | `role_based_access_control` | [NIST RBAC standard](https://csrc.nist.gov/projects/role-based-access-control) | adopted | role |
| `security_and_authorization` | `revocation` | [NIST RBAC standard](https://csrc.nist.gov/projects/role-based-access-control) | adopted | revocation |
| `business_task_completion` | `execution_accuracy` | [Spider: Yale Semantic Parsing and Text-to-SQL](https://yale-lily.github.io/spider) | adopted | chatbi |
| `business_task_completion` | `task_completion` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | 流程完成 |
| `business_task_completion` | `policy_compliance` | [本项目判定器与 BENCHMARK 口径](../../../BENCHMARK.md) | project_defined | after_sales/operations |
| `business_task_completion` | `constraint_satisfaction` | [本项目判定器与 BENCHMARK 口径](../../../BENCHMARK.md) | project_defined | calculation 门槛 |

## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 track |
| Anthropic, Introducing Bloom: Automated behavioral evals | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, Designing AI resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise in agentic coding evals | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |
| RAGAS, List of available metrics | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | 子类术语与判定维度参考 | 对应大类 |
| ARES: An Automated Evaluation Framework for RAG (NAACL 2024) | https://aclanthology.org/2024.naacl-long.20/ | 子类术语与判定维度参考 | 对应大类 |
| RGB: Benchmarking Large Language Models in RAG (AAAI 2024) | https://arxiv.org/abs/2309.01431 | 子类术语与判定维度参考 | 对应大类 |
| RAGBench / TRACe | https://arxiv.org/abs/2407.11005 | 子类术语与判定维度参考 | 对应大类 |
| CRAG: Comprehensive RAG Benchmark | https://arxiv.org/abs/2406.04744 | 子类术语与判定维度参考 | 对应大类 |
| LongMemEval (ICLR 2025) | https://arxiv.org/abs/2410.10813 | 子类术语与判定维度参考 | 对应大类 |
| Knowledge Conflicts for LLMs: A Survey | https://arxiv.org/abs/2403.08319 | 子类术语与判定维度参考 | 对应大类 |
| 本项目判定器与 BENCHMARK 口径 | ../../../BENCHMARK.md | 子类术语与判定维度参考 | 对应大类 |
| Berkeley Function Calling Leaderboard (BFCL) | https://gorilla.cs.berkeley.edu/leaderboard.html | 子类术语与判定维度参考 | 对应大类 |
| OWASP Top 10 for LLM Applications | https://genai.owasp.org/llm-top-10/ | 子类术语与判定维度参考 | 对应大类 |
| NIST RBAC standard | https://csrc.nist.gov/projects/role-based-access-control | 子类术语与判定维度参考 | 对应大类 |
| Spider: Yale Semantic Parsing and Text-to-SQL | https://yale-lily.github.io/spider | 子类术语与判定维度参考 | 对应大类 |
| DeepEval, LLM evaluation metrics | https://deepeval.com/docs/metrics-introduction | 子类术语与判定维度参考 | 对应大类 |

> 本数据集全部为合成数据：不做人工标注、不采集真实日志。确定性 grader 参与门禁；LLM judge 仅作旁路观察，未经人工校准，不阻塞发布。上述来源用于分类口径、评测方法与指标命名，不代表本数据集来自这些基准的原始样本。

> 状态说明：`adopted` 表示子类名直接采用来源术语；`adapted` 表示借用来源术语但判定口径由本项目定义；`project_defined` 表示未检索到官方基准的同名子类，行为以本项目判定器与 `BENCHMARK.md` 为准。

## heldout 使用方式

- 默认报告与 CI 回放**排除** `heldout`（Agent 510/600，RAG 由 `_eligible` 过滤）；
- 发布前复核：Agent 用 `make evals-agent-heldout` 或 `--include-heldout`，RAG 用 `ECOM_EVAL_INCLUDE_HELDOUT=1`；
- heldout 结果不进入公开趋势报告，只用于发布前检查。
