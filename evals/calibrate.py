#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge 校准：把 LLM 判定与人工标注放在一起比，算 Cohen's kappa。

为什么必须先做这一步：生成层的所有分数（faithfulness / relevancy / 通过率）都由
judge 产出。judge 没跟人工对过，就无法区分"系统真的答错了"和"judge 判错了"，
归因链的后三步全部失去依据。业界门槛是 kappa ≥ 0.6 才算可用。

本工具不替人标注——标注必须由人做，否则校准就是自己跟自己比，毫无意义。

两步：
  1) 导出待标注表（默认分层抽样：一半失败样本 + 一半通过样本）
       .venv/bin/python evals/calibrate.py --export 60
     产出 evals/reports/calibration/<时间戳>-sheet.jsonl
  2) 人工把每行 human 的 pass / faithful / relevant 填成 true 或 false
     （拿不准就留 null，评分时自动跳过），然后
       .venv/bin/python evals/calibrate.py --score <sheet.jsonl>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
import metrics  # noqa: E402

EVALS = ROOT / "evals"
REPORTS = EVALS / "reports"
CALIB = REPORTS / "calibration"
CASES = EVALS / "datasets/generated/latest/cases.jsonl"
DIMS = ("pass", "faithful", "relevant")


def latest_report() -> Path:
    files = sorted(REPORTS.glob("*.json"))
    if not files:
        raise SystemExit(f"没有报告：{REPORTS} 为空，先跑一次 --full 或 --behavior-only")
    return files[-1]


def load_cases() -> dict:
    if not CASES.exists():
        return {}
    out = {}
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        out[c["case_id"]] = c
    return out


def export(n: int, seed: int) -> Path:
    """从最新报告抽 N 条生成待标注表。

    一半失败一半通过：只抽失败样本会测不出 judge 的假阴性（该报错没报），
    只抽通过样本则测不出假阳性（没答对却放行）——两侧都得有。
    """
    rep = latest_report()
    data = json.loads(rep.read_text(encoding="utf-8"))
    detail = (data.get("behavior") or {}).get("detail") or []
    usable = [d for d in detail if d.get("answer")]
    if len(usable) < n:
        raise SystemExit(
            f"报告 {rep.name} 里可标注样本只有 {len(usable)} 条（需要 {n}）。\n"
            "带答案的逐例证据是 2026-10-21 改造后才有的：请先跑一次 "
            "`--full` 或 `--behavior-only` 生成新报告。")
    cases = load_cases()
    rnd = random.Random(seed)
    fails = [d for d in usable if not d.get("pass")]
    ok = [d for d in usable if d.get("pass")]
    n_fail = min(len(fails), max(1, n // 2))
    picked = rnd.sample(fails, n_fail) if n_fail else []
    need = n - len(picked)
    if need > 0:
        picked += rnd.sample(ok, min(len(ok), need))
    rnd.shuffle(picked)

    rows = []
    for d in picked:
        c = cases.get(d["id"]) or {}
        rows.append({
            "case_id": d["id"],
            "category": c.get("category"),
            "question": c.get("question"),
            "expected_behavior": c.get("expected_behavior"),
            "answer": d.get("answer"),
            "citations": d.get("citations") or [],
            "judge": d.get("verdict") or {},
            "judge_reason": d.get("why"),
            "human": {k: None for k in DIMS},
            "notes": "",
        })
    CALIB.mkdir(parents=True, exist_ok=True)
    path = CALIB / f"{time.strftime('%Y%m%d-%H%M%S')}-sheet.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def score(path: Path) -> dict:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = {"sheet": path.name, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "rows": len(rows), "dims": {}}
    for dim in DIMS:
        pairs = [(r.get("human", {}).get(dim), (r.get("judge") or {}).get(dim)) for r in rows]
        pairs = [(h, j) for h, j in pairs if h is not None and j is not None]
        if len(pairs) < 10:
            out["dims"][dim] = {"n": len(pairs), "skipped": "有效标注不足 10 条"}
            continue
        out["dims"][dim] = metrics.cohen_kappa([h for h, _ in pairs], [j for _, j in pairs])
    return out


def print_score(out: dict) -> None:
    print(f"\n标注文件 {out['sheet']}（{out['rows']} 行）")
    print(f"\n{'维度':<10}{'n':>4}{'kappa':>8}{'95%CI':>18}{'一致率':>8}  结论")
    for dim in DIMS:
        r = out["dims"].get(dim) or {}
        if r.get("skipped"):
            print(f"{dim:<10}{r['n']:>4}{'—':>8}{'—':>18}{'—':>8}  {r['skipped']}")
            continue
        ci = "—" if r.get("ci95_low") is None else f"[{r['ci95_low']:.2f}, {r['ci95_high']:.2f}]"
        print(f"{dim:<10}{r['n']:>4}{r['kappa']:>8.3f}{ci:>18}{r['agreement']:>8.3f}  {r['verdict']}")

    print("\n混淆矩阵（行=人工，列=judge）")
    for dim in DIMS:
        r = out["dims"].get(dim) or {}
        if not r.get("matrix"):
            continue
        labels = r["labels"]
        head = "".join(f"{x:>8}" for x in labels)
        print(f"\n[{dim}]\n{'':<8}{head}")
        for lab, row in zip(labels, r["matrix"]):
            print(f"{lab:<8}" + "".join(f"{v:>8}" for v in row))

    scored = {d: v for d, v in out["dims"].items() if (v or {}).get("kappa") is not None}
    low = [d for d, v in scored.items() if v["kappa"] < 0.6]
    high = [d for d, v in scored.items() if v["kappa"] >= 0.6]
    print()
    if low:
        print(f"⛔ kappa < 0.6，这些维度的 judge 分数不能作门禁依据：{', '.join(low)}")
        print("   下一步：把判定问题窄化成二元（如「这条声明是否被引用原文支持」）、")
        print("   确认 judge 拿得到证据（引用必须带正文）、或更换 judge 模型后重标。")
    elif high:
        print(f"✅ kappa ≥ 0.6，judge 判定可用作门禁依据：{', '.join(high)}")
    else:
        print("⚠ 没有可评估的维度：先完成人工标注再跑 --score")


def main():
    ap = argparse.ArgumentParser(description="judge 校准：人工标注 vs LLM 判定的一致性")
    ap.add_argument("--export", type=int, metavar="N", help="从最新报告抽 N 条生成待标注表")
    ap.add_argument("--score", metavar="FILE", help="对已标注的 sheet 计算 kappa")
    ap.add_argument("--seed", type=int, default=20261021, help="抽样种子（固定可复现）")
    args = ap.parse_args()

    if args.export:
        path = export(args.export, args.seed)
        print(f"待标注表已生成：{path}")
        print("请把每行 human 的 pass / faithful / relevant 填成 true 或 false"
              "（拿不准留 null），然后运行：")
        print(f"  .venv/bin/python evals/calibrate.py --score {path}")
        return
    if args.score:
        out = score(Path(args.score))
        print_score(out)
        CALIB.mkdir(parents=True, exist_ok=True)
        dst = CALIB / (Path(args.score).stem + "-kappa.json")
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已存档：{dst}")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
