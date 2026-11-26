# evals/agent_eval/ — 自治多智能体调研系统评测维护约定与记录路由

> 本文件仅用于维护**第二套：市场调研自治多智能体系统**（`src/taozhi/application/research/`、`src/taozhi/core/`、`evals/agent_eval/`）的评测标准。  
> 与**第一套：企业知识库问答 & 受控问数 Workflow**（`src/taozhi/application/rag/`、`src/taozhi/infrastructure/knowledge/`、`../rag_eval/`、`../rag_eval/测评记录.md`）**完全独立、互不混用数据集、判定器与基线**。  
> 本文件随仓库版本化，作为该子系统的评测维护约定。

---

## 1. 测试目的（多智能体核心质量目标）

1. **工具契约与边界隔离防越权（零容忍）**
   - 严格校验 `required_tools`（如比价必须调用 `1688` 与 `Amazon`）与 `forbidden_tools`（如纯舆情任务严禁越权触发电商货源接口），通过白盒轨迹追踪防止 Over-Planning 越界调用。
2. **反虚假与可观测白盒审计（Zero Fake）**
   - 严禁出现虚假思考步骤文案或伪造数据条数（`AntiHallucinationJudge`）；所有工具调用次数、有效返回条数、子智能体数与 TraceID 必须完整可回放。
3. **动态中途干预采纳与实时重规划**
   - 验证运行期注入 Steering 指令后，主脑与子智能体能真实调整后续规划并在最终研报中兑现要求（`SteeringJudge`）。
4. **交付研报完整性与异常降级自愈**
   - 验证终稿交付物满足字数与核心大纲小节覆盖（`DeliverableJudge`），且在冷门品类货源稀缺或单数据源受限时能客观降级汇报风险而不捏造数据。

---

## 2. 评测记录路由（Mandatory Routing）

- **唯一多智能体测评记录文件**：[`./测评记录.md`](./测评记录.md)（相对路由 `./测评记录.md`）
- **强制更新触发条件**：
  1. 对 `src/taozhi/orchestration/`、`src/taozhi/core/`（agent/tools/memory）、`evals/agent_eval/` 进行任何流程、路由、工具守卫或判定器修改；
  2. 执行 `make evals-agent` 或 `.venv/bin/python -m evals.agent_eval.runner`（含 `--case <ID>` 单例复测或全量跑批）。

---

## 3. 多智能体评测体系与判定器口径

> 用例、沙箱、判定器与运行器唯一口径源：[`./cases.py`](./cases.py)、[`./harness.py`](./harness.py)、[`./judges.py`](./judges.py)、[`./runner.py`](./runner.py)。

| 维度 / 组件 | 覆盖范围与数据源 | 核心指标与规则 | 门禁性质 |
|---|---|---|---|
| **基准用例集（`cases.py`）** | 当前 600 例合成回放（6 大类 × 6 用途，含 60 红队）+ 10 例实测；日常回放默认排除 90 例 heldout，发布前用 `--include-heldout` 跑全量；子类命名见 `evals/naming.py` | `total_cases`、`passed_cases`、`pass_rate`、`mean_score` | 全量跑批 `pass_rate < 0.75` 时 `runner.py` 退出码 `1` 阻塞 |
| **工具契约判定（`ToolExecutionJudge`）** | `AgentRunResult.tool_calls` | `required_tools` 命中率、`forbidden_tools` 零触发、有效数据条数 `>= min_data_count` | 触发禁止工具即判 `FAIL` |
| **交付与真实性（`DeliverableJudge` & `AntiHallucinationJudge`）** | `AgentRunResult.report` | 核心大纲小节正则匹配、`len(report) >= min_chars`、虚假思考标记扫描 | 出现虚假思考文案直接判 `FAIL` |
| **动态干预判定（`SteeringJudge`）** | `steering_applied` & `steering_keyword` | 注入干预后终稿关键词与重规划兑现验证 | 干预用例未采纳即判 `FAIL` |
| **时延与追踪日志（`runner.py`）** | `evals/reports/agent_eval/` + Arize Phoenix | `latency_p50_seconds`、`latency_p95_seconds`、`trace_id`、`subagents_count`、`tool_calls_count` | 追踪记录全量落盘，异常延迟归因分析 |

---

## 4. `测评记录.md` 强制记录规范（Audit Contract）

向 `./测评记录.md` 追加或更新任何一次多智能体测评记录时，**必须完整包含以下 6 个栏目**，严禁虚构指标或略去 Trace 证据：

1. **测试元信息**：测试时间、Git SHA、执行命令（全量 `make evals-agent` 或 `--case <ID>`）、报告路径（`evals/reports/agent_eval/agent_eval_report_<ts>.json`）与遥测观察流路径（`agent_observations_<ts>.jsonl`）。
2. **基线对比指标**：明确写出本次对比的上一轮全量/用例级结果（`pass_rate`、`mean_score`、`P50/P95` 耗时与对应 SHA）。
3. **使用的方法与具体改动**：逐文件说明对 Supervisor 路由、子智能体工具、Tool Guard、Prompt 或 Judge 口径的修改细节。
4. **本次实测指标与判定明细**：如实列出全量或用例级的 `pass_rate`、`mean_score`、P50/P95 耗时、4 大 Judge 逐项得分及关键遥测 JSON 快照。
5. **白盒 Badcase 归因分类**：按执行轨迹精确定位根因分类（`工具越权调用` / `漏调必需工具` / `动态干预未采纳` / `研报缺节或过短` / `虚假思考文案` / `数据源降级异常`），定位到具体源码模块与分支条件。
6. **结论与修复行动项**：明确通过/阻塞结论、修复步骤与回归验证命令（如 `.venv/bin/python -m evals.agent_eval.runner --case <ID>` 与全量 `make evals-agent`）。

---

## 5. 执行与纪律红线

- **真实执行与可回放**：文档中出现的一切分数、耗时、工具调用序列与 TraceID 必须直接源自 `evals/reports/agent_eval/` 真实生成文件，严禁脑补、截断捏造或伪造成功。
- **局部与全量严格区分**：`--case` 单例复测仅作问题定位与修复确认；全量评估结论必须运行完整 `AGENT_BENCHMARK_CASES` 并通过 `pass_rate >= 0.75` 门禁。
- **不混用两套体系**：多智能体与 RAG 评测完全独立，不共用基线批次或本机基线缓存，且仅修改本目录 `./测评记录.md`。
- **数据集版本目录规范**：基准测试集按 `evals/datasets/agent_v3_20261124/`（历史 v1/v2 只读）分版本只读留存，每个目录须配备 `README.md` 说明用例分布与口径，严禁原地覆盖历史版本。
- **V4 回归集**：`evals/datasets/agent_v4_20261125/` 为显式 `--suite v4` 小规模回归集，不接管 V3 默认目录；运行 `make evals-agent-v4`，V3 仍走 `make evals-agent-replay`。
- **入库入口唯一**：报告入库统一走 `python -m evals.agent_eval.publish`（内部委托 `evals/center`），保留数据集版本与算法版本归属；不得另建写库入口。
