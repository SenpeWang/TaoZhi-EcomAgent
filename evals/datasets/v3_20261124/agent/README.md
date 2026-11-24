# V3 Agent 评测数据集（`v3_20261124/agent`）

多智能体市场调研全合成评测集。6 个能力大类 × 6 种用途，约 600 例（另有 10 例实测）。

## 文件清单

| 文件 | 内容 |
|---|---|
| `research_cases.json` | 约 600 例（6 大类 × 90 + 红队 60） |
| `research_replay.json` | 与用例一一对应的标准观测 |
| `research_counterexamples.json` | 每条用例 1 条定向反例 |
| `web_replay.json` | AGENT-WEB-003 边界样本 |

`evals/agent_eval/cases.py` 另有 10 例实测（live）。

## 字段

`id` / `capability`（大类）/ `category`（子类）/ `track`（用途）/ `topic`（场景）/
`prompt` / `tool_expectations` / `tool_order` / `reference_facts` / `forbidden_claims` /
`required_sections` / `min_chars` / `max_tool_calls` / `max_elapsed_seconds` / `turns`。

## 规模与用途

| 用途 track | 每大类 | 说明 |
|---|---:|---|
| golden | 30 | 冻结参考集 |
| capability | 20 | 能力提升集 |
| regression | 15 | 回归集 |
| challenge | 10 | 挑战集 |
| heldout | 15 | 留出集 |
| redteam | 60（独立） | 注入/越权/泄漏/恶意任务拒绝 |

## 子类命名登记表

| 大类 capability | 子类 subcategory | 官方术语来源 | 状态 | v2 旧名 |
|---|---|---|---|---|
| `task_completion` | `task_completion` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | task_completion |
| `task_completion` | `groundedness` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | evidence_attribution |
| `task_completion` | `hallucination` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | hallucination_control |
| `task_completion` | `plan_quality` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | 报告完整性 |
| `tool_use` | `tool_correctness` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | tool_selection |
| `tool_use` | `argument_correctness` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | argument_correctness |
| `tool_use` | `tool_call_accuracy` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | 工具选择准确率 |
| `tool_use` | `tool_call_f1` | [RAGAS, List of available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) | adopted | 工具调用 F1 |
| `tool_use` | `parallel_tool_calls` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | 并行调用 |
| `planning_and_adaptation` | `plan_adherence` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | dependency_ordering |
| `planning_and_adaptation` | `step_efficiency` | [DeepEval, LLM evaluation metrics](https://deepeval.com/docs/metrics-introduction) | adopted | cost_efficiency 步数部分 |
| `planning_and_adaptation` | `error_recovery` | [本项目判定器与 BENCHMARK 口径](../../../BENCHMARK.md) | project_defined | error_recovery |
| `planning_and_adaptation` | `mid_trajectory_steering` | [本项目判定器与 BENCHMARK 口径](../../../BENCHMARK.md) | project_defined | dynamic_steering |
| `security_and_governance` | `prompt_injection` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | prompt_injection |
| `security_and_governance` | `sensitive_information_disclosure` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | 数据外泄 |
| `security_and_governance` | `system_prompt_leakage` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | 新增 |
| `security_and_governance` | `excessive_agency` | [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | adopted | access_control |
| `security_and_governance` | `harmful_task_refusal` | [AgentHarm](https://arxiv.org/abs/2410.09024) | adopted | refusal |
| `interaction_and_memory` | `multi_turn` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | multi_turn |
| `interaction_and_memory` | `memory` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | 跨会话记忆 |
| `interaction_and_memory` | `information_extraction` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | 记忆抽取 |
| `interaction_and_memory` | `multi_session_reasoning` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | 跨会话推理 |
| `interaction_and_memory` | `knowledge_updates` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | 状态更新 |
| `interaction_and_memory` | `abstention` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | empty_result/拒答 |
| `interaction_and_memory` | `irrelevance` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | 不相关工具/不调用 |
| `interaction_and_memory` | `clarification` | [Learning to Ask Good Questions: Ranking Clarification Questions using NEVI](https://arxiv.org/abs/1805.04655) | adapted | ambiguity_clarification |
| `efficiency_and_reliability` | `latency` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | 时延 |
| `efficiency_and_reliability` | `cost` | [Berkeley Function Calling Leaderboard (BFCL)](https://gorilla.cs.berkeley.edu/leaderboard.html) | adopted | 成本 |
| `efficiency_and_reliability` | `numerical_reasoning` | [GSM8K: Training Verifiers to Solve Math Word Problems](https://arxiv.org/abs/2110.14168) | adopted | numerical_reasoning |
| `efficiency_and_reliability` | `temporal_reasoning` | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | adopted | temporal_reasoning |
| `efficiency_and_reliability` | `consistency` | [τ-bench](https://arxiv.org/abs/2406.12045) | adapted | pass^k |

## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 track |
| Anthropic, Introducing Bloom: Automated behavioral evals | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, Designing AI resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise in agentic coding evals | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |
| DeepEval, LLM evaluation metrics | https://deepeval.com/docs/metrics-introduction | 子类术语与判定维度参考 | 对应大类 |
| RAGAS, List of available metrics | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | 子类术语与判定维度参考 | 对应大类 |
| Berkeley Function Calling Leaderboard (BFCL) | https://gorilla.cs.berkeley.edu/leaderboard.html | 子类术语与判定维度参考 | 对应大类 |
| 本项目判定器与 BENCHMARK 口径 | ../../../BENCHMARK.md | 子类术语与判定维度参考 | 对应大类 |
| OWASP Top 10 for LLM Applications | https://genai.owasp.org/llm-top-10/ | 子类术语与判定维度参考 | 对应大类 |
| AgentHarm | https://arxiv.org/abs/2410.09024 | 子类术语与判定维度参考 | 对应大类 |
| LongMemEval (ICLR 2025) | https://arxiv.org/abs/2410.10813 | 子类术语与判定维度参考 | 对应大类 |
| Learning to Ask Good Questions: Ranking Clarification Questions using NEVI | https://arxiv.org/abs/1805.04655 | 子类术语与判定维度参考 | 对应大类 |
| GSM8K: Training Verifiers to Solve Math Word Problems | https://arxiv.org/abs/2110.14168 | 子类术语与判定维度参考 | 对应大类 |
| τ-bench | https://arxiv.org/abs/2406.12045 | 子类术语与判定维度参考 | 对应大类 |

> 本数据集全部为合成数据：不做人工标注、不采集真实日志。确定性 grader 参与门禁；LLM judge 仅作旁路观察，未经人工校准，不阻塞发布。上述来源用于分类口径、评测方法与指标命名，不代表本数据集来自这些基准的原始样本。

> 状态说明：`adopted` 表示子类名直接采用来源术语；`adapted` 表示借用来源术语但判定口径由本项目定义；`project_defined` 表示未检索到官方基准的同名子类，行为以本项目判定器与 `BENCHMARK.md` 为准。

## heldout 使用方式

- 默认报告与 CI 回放**排除** `heldout`（Agent 510/600，RAG 由 `_eligible` 过滤）；
- 发布前复核：Agent 用 `make evals-agent-heldout` 或 `--include-heldout`，RAG 用 `ECOM_EVAL_INCLUDE_HELDOUT=1`；
- heldout 结果不进入公开趋势报告，只用于发布前检查。
