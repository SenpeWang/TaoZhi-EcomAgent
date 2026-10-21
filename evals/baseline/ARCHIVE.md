# 基线归档（只读）

本目录存放每次全量跑批产生的基线快照，**归档后不得编辑、覆盖或删除**。
要更新基线：跑 `make evals-baseline` 生成新的 `evals/baseline.json`，
再执行 `make evals-archive` 追加一条新快照；旧快照一律保留。

文件名 = 该次跑批的开跑时间（`%Y%m%d-%H%M%S`），与 Phoenix experiment 名
`eval-<开跑时间>-<git>` 对齐，横向对比时不用做映射。

校验：`make evals-archive-check`（逐条比对 md5，不符则退出码 1）。

| 快照 | 跑批开始 | git | 对应报告 | md5 | 门禁 |
|---|---|---|---|---|---|
| `20261021-182903.json` | 2026-10-21 18:29:03 | 66bc187 | `20261021-225329-66bc187.json` | `cdd9b3eaf89c62f979b0ada2b5338af0` | faith 0.981✅ / rel 0.598⚠ / recall@8 0.884✅ |
