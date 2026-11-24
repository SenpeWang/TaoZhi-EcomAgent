# V3 基准评测数据集（`v3_20261124`）

本目录为 V3 全合成基准数据集，按 `rag/` 与 `agent/` 两个体系分开存放。每个体系 6 个能力大类，
每个大类下按 6 种用途（golden / capability / regression / challenge / heldout / redteam）组织，
合计约 600 例/体系（含独立红队 60 例）。

## 目录结构

| 子目录 | 体系 | 规模 | 说明 |
|---|---|---|---|
| `rag/` | 企业知识问答（RAG + ChatBI） | 约 600 例 | 见 [rag/README.md](rag/README.md) |
| `agent/` | 多智能体调研 | 约 600 例 + 10 例实测 | 见 [agent/README.md](agent/README.md) |

## 设计口径

核对清单（规模、大类分布、抽样与已知边界）见 [`REVIEW.md`](REVIEW.md)。

- 大类按能力划分，不按业务主题；业务主题放 `topic` 维度。
- 子类英文名对齐官方来源术语，登记见 `rag/README.md` 与 `agent/README.md`。
- 用例角色体现在 `track` 字段；held-out 不进入公开报告。
- 指标：`pass@k` 与 `pass^k` 同时报告；子类 n<30 不单独下结论。


## 参考来源与依据

| 来源 | 链接 | 采用内容 | 影响范围 |
|---|---|---|---|
| Anthropic, Demystifying evals for AI agents | https://www.anthropic.com/research/demystifying-evals-for-ai-agents | task/trial/grader、capability vs regression、pass@k/pass^k | 全部大类与 split |
| Anthropic, Bloom | https://www.anthropic.com/research/bloom | 场景生成、seed 复现、judge 校准方法 | 生成器/校准设计 |
| Anthropic, AI-resistant technical evaluations | https://www.anthropic.com/research/AI-resistant-technical-evaluations | 任务代表性与难度设计 | capability/challenge |
| Anthropic, Infrastructure noise | https://www.anthropic.com/research/infrastructure-noise | 环境与资源预算一致性 | harness |
| Anthropic, Define success criteria and build evaluations | https://platform.claude.com/docs/en/test-and-evaluate/develop-tests | 任务分布、边界用例、自动评分优先 | 全部 |

> 本数据集全部为合成数据：不做人工标注、不采集真实日志。确定性 grader 参与门禁；
> LLM judge 仅作旁路观察，未经人工校准，不阻塞发布。上述来源用于分类口径、
> 评测方法与指标命名，不代表本数据集来自这些基准的原始样本。

## heldout 使用方式

- 默认报告与 CI 回放**排除** `heldout`（Agent 510/600，RAG 由 `_eligible` 过滤）；
- 发布前复核：Agent 用 `make evals-agent-heldout` 或 `--include-heldout`，RAG 用 `ECOM_EVAL_INCLUDE_HELDOUT=1`；
- heldout 结果不进入公开趋势报告，只用于发布前检查。
