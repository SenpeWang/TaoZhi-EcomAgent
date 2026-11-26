# V4 Agent 小规模评测集（agent_v4_20261125）

24 例合成回放，覆盖速答路由、协调员直接工具、工具自主选择与审计角色。

| 文件 | 内容 |
|---|---|
| `research_cases.json` | 24 例 |
| `research_replay.json` | 对应观测（含 `subagent_roles`/`evidence_roles`）|
| `research_counterexamples.json` | 反例占位（V4 暂不生成）|

运行：`python -m evals.agent_eval.runner --suite v4 --replay evals/datasets/agent_v4_20261125/research_replay.json`

V3 数据集保持只读；V4 不影响 V3 默认回放。
