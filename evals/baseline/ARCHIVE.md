# 基线快照（可修订）

本目录存放每次全量跑批的基线数据，用于记录代码逐步演进；**允许修订快照内容**。
要更新记录：跑 `make evals-baseline` 生成/更新 `evals/baseline.json`，再执行
`make evals-archive` 写入或更新对应快照；修订后的 md5 由 `make evals-archive-check` 同步登记。

文件名 = 该次跑批的开跑时间（`%Y%m%d-%H%M%S`），与 Phoenix experiment 名
`eval-<开跑时间>-<git>` 对齐，横向对比时不用做映射。

| 快照 | 跑批开始 | git | 对应报告 | md5 | 门禁 |
|---|---|---|---|---|---|
| `20261021-182903.json` | 2026-10-21 18:29:03 | 66bc187 | `20261021-225329-66bc187.json` | `cdd9b3eaf89c62f979b0ada2b5338af0` | faith 0.981✅ / rel 0.598⚠ / recall@8 0.884✅ |
| `20261117-001401.json` | 2026-11-17 00:14:01 | 687f026 | — | `0cc50838a5ed86734c75a05783548fc1` | faith 0.924✅ / rel 0.904✅ / recall@8 0.888✅ |
