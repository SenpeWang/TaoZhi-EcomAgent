# evals/legacy/ — 已退役的评估原型

这些文件是 `run_evals.py` 成型之前的早期脚本，**已被取代，不再维护，也不要在它们上面改逻辑**。

| 文件 | 现状 |
|---|---|
| `eval_retrieval.py` | 检索层原型 → 已并入 `run_evals.py` 的 `phase_retrieval` |
| `eval_adversarial.py` | 对抗层原型 → 已并入 `run_evals.py` 的 `phase_adversarial` |
| `eval_enterprise.py` | 检索+行为合并原型 → 已并入 `run_evals.py` 的 `phase_behavior` |
| `cases_part1.jsonl` / `cases_part2.jsonl` | 评测集分批交付记录；正式版是 `datasets/cases.jsonl`（两批合并去重） |

保留原因：历史可追溯（谁在什么时候用什么口径测过），以及交付记录留痕。

注意：这几个脚本里的路径已统一改为**相对项目根解析**（`Path(__file__).resolve().parents[2]`），
数据与报告指向迁移后的 `evals/datasets/` 与 `evals/reports/`；但接口仍是早期版本（如旧端口 18501），
未做适配，因此不保证可运行，也不需要跑。要复现当期结论请看 `reports/legacy/` 里对应的报告文件。
