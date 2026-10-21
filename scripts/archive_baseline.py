#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前 evals/baseline.json 归档成只读快照，进 Git 留档。

为什么要有归档目录
------------------
``baseline.json`` 是"当前基线"，每次 ``make evals-baseline`` 都会整份覆盖，
历史值只活在 git log 里——想对比某次跑批，得先翻提交、再认出哪个 commit
是基线更新。归档目录把这个成本摊平：一次跑批一个快照文件，文件名就是开跑
时间，与 Phoenix experiment 名 ``eval-<开跑时间>-<git>`` 对齐。

只读怎么保证
------------
写完 chmod 444，并把 md5、git、六项指标摘要记进 ARCHIVE.md。校验命令
``make evals-archive-check`` 会重新算一遍 md5 逐条比对——不是防人，是防"某次
手改了基线却没人发现"。

用法::

    python scripts/archive_baseline.py archive   # 归档当前 baseline.json（默认）
    python scripts/archive_baseline.py check     # 校验所有快照未被改动
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evals/baseline.json"
ARCHIVE_DIR = ROOT / "evals/baseline"
REGISTER = ARCHIVE_DIR / "ARCHIVE.md"

HEADER = """# 基线归档（只读）

本目录存放每次全量跑批产生的基线快照，**归档后不得编辑、覆盖或删除**。
要更新基线：跑 `make evals-baseline` 生成新的 `evals/baseline.json`，
再执行 `make evals-archive` 追加一条新快照；旧快照一律保留。

文件名 = 该次跑批的开跑时间（`%Y%m%d-%H%M%S`），与 Phoenix experiment 名
`eval-<开跑时间>-<git>` 对齐，横向对比时不用做映射。

校验：`make evals-archive-check`（逐条比对 md5，不符则退出码 1）。

| 快照 | 跑批开始 | git | 对应报告 | md5 | 门禁 |
|---|---|---|---|---|---|
"""


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def snapshot_key(report: dict) -> str:
    """快照名：取报告里的开跑时间，与报告/experiment 命名同源。"""
    at = report.get("at") or ""
    try:
        return datetime.strptime(at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d-%H%M%S")
    except ValueError:
        return datetime.fromtimestamp(BASELINE.stat().st_mtime).strftime("%Y%m%d-%H%M%S")


def gate_summary(report: dict) -> str:
    """门禁摘要：硬底线是否过、目标线差哪几项。纯读报告，不调 LLM。

    与 run_evals.gate_check 用同一套阈值：这里只做登记展示，判定仍以
    run_evals 的退出码为准。
    """
    six = report.get("six_metrics") or {}
    faith = (six.get("faithfulness") or {}).get("micro")
    relev = (six.get("answer_accuracy") or {}).get("relevant_rate")
    recall = six.get("recall@k") or {}
    recall8 = recall.get(8, recall.get("8"))
    parts = []
    if faith is not None:
        parts.append(f"faith {faith:.3f}{'✅' if faith >= 0.85 else '⛔'}")
    if relev is not None:
        parts.append(f"rel {relev:.3f}{'✅' if relev >= 0.80 else '⚠'}")
    if recall8 is not None:
        parts.append(f"recall@8 {recall8:.3f}{'✅' if recall8 >= 0.80 else '⚠'}")
    return " / ".join(parts) or "—"


def cmd_archive(report_name: str = "") -> int:
    if not BASELINE.is_file():
        sys.exit(f"没有基线文件：{BASELINE}\n先跑 `make evals-baseline`。")
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    name = f"{snapshot_key(report)}.json"
    target = ARCHIVE_DIR / name
    if target.exists():
        # 归档的意义就是不可变。重跑同一时刻还想覆盖，等于把"当时跑出来是什么"抹掉。
        sys.exit(f"快照已存在，拒绝覆盖：{target}\n"
                 "确认这是新的一次跑批再归档；否则请手动删除旧快照。")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    # 用 copyfile 而不是重新 dump：保证与 baseline.json 逐字节一致，
        # 这样 md5 才能作为"是否被改动"的判据，不受 JSON 序列化差异干扰。
    shutil.copyfile(BASELINE, target)
    digest = md5_of(target)
    target.chmod(0o444)          # 只读：防止手误改写历史基线

    fresh = not REGISTER.exists()
    src = report_name or "—"
    with REGISTER.open("a", encoding="utf-8") as f:
        if fresh:
            f.write(HEADER)
        f.write(f"| `{name}` | {report.get('at') or '—'} | {report.get('git') or '—'} "
               f"| {src} | `{digest}` | {gate_summary(report)} |\n")
    print(f"已归档：{target}")
    print(f"md5    ：{digest}")
    print(f"登记   ：{REGISTER}")
    return 0


def cmd_check() -> int:
    if not REGISTER.is_file():
        sys.exit(f"没有归档登记：{REGISTER}")
    rows = [l for l in REGISTER.read_text(encoding="utf-8").splitlines()
            if l.startswith("| `")]
    if not rows:
        sys.exit("归档登记里没有快照记录。")
    bad = []
    for line in rows:
        cols = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        name, want = cols[0], (cols[4] if len(cols) > 4 else "")
        path = ARCHIVE_DIR / name
        if not path.is_file():
            bad.append(f"{name}：文件缺失")
            continue
        got = md5_of(path)
        if want and got != want:
            bad.append(f"{name}：md5 不符（登记 {want[:8]}… / 实际 {got[:8]}…）")
    for line in rows:
        print(line)
    if bad:
        print("\n⛔ 归档完整性校验失败：")
        for b in bad:
            print("  -", b)
        return 1
    print(f"\n✅ {len(rows)} 份快照与登记一致，未被改动")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "archive"
    if cmd == "archive":
        return cmd_archive(sys.argv[2] if len(sys.argv) > 2 else "")
    if cmd == "check":
        return cmd_check()
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
