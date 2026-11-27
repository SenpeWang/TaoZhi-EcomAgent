# evals/ 模块维护约定

本文件是 evals/ 的维护约定，与根 AGENTS.md 同级，随仓库版本化。

## 分工（不新增第五份 md，口径散落必互相矛盾）

- 公开文档三份：`README.md` 讲怎么跑，`BENCHMARK.md` 是口径唯一来源，
  `dataset/README.md` 讲数据包。
- 指标实现只有 `metrics.py`，运行器当前实现只有 `rag_eval/run.py`；第二套实现与一次性探针一律删除。
- **结果必须真实**：评测中心只接收真实执行（`live`）结果；合成回放/轨迹重评只用于本地与 CI 回归，不进入中心（`publish` 拒绝，触发接口返回 `REAL_MODE_ONLY`）。
- 指标展示目录只有 `src/taozhi/domain/evaluation_metrics.py`：同一评测类型+口径的指标键固定，界面与接口按 `/api/evaluations/metrics/catalog` 渲染，禁止前端自拼或改名；数据集展示必须使用登记原始用例，不得裁剪字段。
  结论回写代码注释后删除，不留副本。

## 流程引用（不在本文件复述）

- 改动验收一律走 `README.md`「改动后的验证流程」；改什么跑什么见 BENCHMARK F2。
- 刷新基线 = 宣布新水位，提交信息写明旧/新水位与原因（要求见 README）。
- 口径变更顺序：改 BENCHMARK → 改代码 → 重跑验收 → 同一提交落地。

## 本模块纪律

- 新用例只走 `dataset/spec` → `generate` → `seed`，且数据集按 `datasets/v1_<时间戳>/`、`datasets/v2_<时间戳>/` 分版本目录存放，每个版本目录及子目录须附带 `README.md`，严禁原地覆盖已定版历史版本。
- `reports/` 产物与本文件不进 Git；`datasets/`、`baseline.json` 与 `evals/baseline/` 随代码进 Git（基线快照记录演进、允许修订）；门禁对比的权威 active 基线以评测中心为准。
- 提交类型常用 `docs(evals)` / `test(evals)` / `fix(evals)` / `chore(evals)`。
- 删除指标、评估层或数据层前确认无引用，并同步 BENCHMARK、README 与 Makefile 目标；
  删除不留 .bak。
