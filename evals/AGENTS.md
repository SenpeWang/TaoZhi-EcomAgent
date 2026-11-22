# evals/ 模块维护约定

本文件是 evals/ 的维护约定，与根 AGENTS.md 同级，随仓库版本化。

## 分工（不新增第五份 md，口径散落必互相矛盾）

- 公开文档三份：`README.md` 讲怎么跑，`BENCHMARK.md` 是口径唯一来源，
  `dataset/README.md` 讲数据包。
- 指标实现只有 `metrics.py`，运行器只有 `run_evals.py`；第二套实现与一次性探针
  结论回写代码注释后删除，不留副本。

## 流程引用（不在本文件复述）

- 改动验收一律走 `README.md`「改动后的验证流程」；改什么跑什么见 BENCHMARK F2。
- 刷新基线 = 宣布新水位，提交信息写明旧/新水位与原因（要求见 README）。
- 口径变更顺序：改 BENCHMARK → 改代码 → 重跑验收 → 同一提交落地。

## 本模块纪律

- 新用例只走 `dataset/spec` → `generate` → `seed`，不手改 `datasets/generated/latest/`。
- `reports/` 产物与本文件不进 Git；`baseline.json` 与 `datasets/` 随代码进 Git。
- 提交类型常用 `docs(evals)` / `test(evals)` / `fix(evals)` / `chore(evals)`。
- 删除指标、评估层或数据层前确认无引用，并同步 BENCHMARK、README 与 Makefile 目标；
  删除不留 .bak。
