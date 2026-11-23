# V1 Agent 调研评测数据集（`v1_20261123/agent`）

本目录存放 **多智能体协同调研（Supervisor Loop）** 的 V1 基准数据集，覆盖工具边界隔离、多源比价、缺口拒答、运行期中途干预（Steering）与反例门禁校验。

## 文件清单

| 文件名 | 用途 | 核心校验维度 |
|---|---|---|
| `research_cases.json` | 12 例固定证据合成基准用例 | 覆盖 MOQ 阶梯价、汇率换算、范围边界、空结果、超时与状态冲突 |
| `research_replay.json` | 12 例对应的标准合成执行观测轨迹 | 工具选型、参数约束、执行时序、引用证据 ID 与最终报告 |
| `research_counterexamples.json` | 定向破坏单步观测的反例集 | 验证 6 类确定性 Judges 能精准拦截违规调用与虚假陈述并触发非零门禁 |
| `web_replay.json` | 全网网页调研边界样本（`AGENT-WEB-003`） | 验证纯全网情报场景严禁越权调用 1688 / Amazon 工具 |

## 常用执行入口

```bash
make evals-agent-replay
```
