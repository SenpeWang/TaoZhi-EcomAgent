# V2 Agent 调研评测数据集（`v2_20261124/agent`）

本目录存放 **多智能体协同调研（Supervisor Loop）** 的 V2 基准数据集，分类口径使用业界标准能力名称，覆盖工具契约、边界隔离、错误恢复、安全治理与交付质量。

## 文件清单

| 文件名 | 用途 | 规模 |
|---|---|---|
| `research_cases.json` | 固定证据合成基准用例 | 190 例 |
| `research_replay.json` | 与用例一一对应的标准执行观测轨迹 | 190 条 |
| `research_counterexamples.json` | 定向破坏单步观测的反例集 | 190 条 |
| `web_replay.json` | 全网网页调研边界样本（`AGENT-WEB-003`） | 1 条 |

`evals/agent_eval/cases.py` 另有 10 例实测（live）用例：`multi_step_planning` 6、`tool_selection` 2、`dynamic_steering` 1、`error_recovery` 1，合计 **200 例**。

合成回放只用于本地与 CI 回归，不进入评测中心；评测中心只接收真实执行（`live`）批次。

## 能力分类（20 类标准名）

| 分组 | 类别 |
|---|---|
| 工具与轨迹 | `tool_selection`、`argument_correctness`、`multi_step_planning`、`dependency_ordering`、`error_recovery` |
| 边界与异常 | `empty_result`、`boundary_value`、`ambiguity_clarification`、`refusal`、`conflict_resolution` |
| 交互与治理 | `dynamic_steering`、`multi_turn`、`access_control`、`prompt_injection`、`hallucination_control`、`evidence_attribution` |
| 交付与质量 | `task_completion`、`cost_efficiency`、`numerical_reasoning`、`temporal_reasoning` |

## 反例覆盖（15 类失败类型）

`missing_observation`、`run_status`、`tool_forbidden`、`tool_missing`、`tool_arguments`、`tool_status`、`tool_data`、`tool_order`、`deliverable_structure`、`fabricated_trace`、`steering`、`reference_fact`、`evidence_citation`、`forbidden_claim`、`budget_exceeded`。

## 常用执行入口

```bash
make evals-agent-replay
python -m evals.agent_eval.runner --dataset synthetic --replay evals/datasets/v2_20261124/agent/research_replay.json
```

## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 split |
| Anthropic, Bloom | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, AI-resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |
| BFCL / AgentBench / τ-bench / DeepEval / AgentHarm | https://gorilla.cs.berkeley.edu/leaderboard.html | 子类口径参考（v3 起正式对齐） | Agent |

## 命名口径说明

v2 使用业务/能力混合分类；v3 起改为"6 大类 capability + 官方术语子类 subcategory + 业务主题 topic"，
完整映射见 [`v3_20261124/agent/README.md`](../../v3_20261124/agent/README.md)。
v2 数据保持只读，不回溯重命名。
