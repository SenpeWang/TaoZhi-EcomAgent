#!/usr/bin/env python3
"""管理基线快照：把当前 evals/baseline.json 归档/更新到 evals/baseline/，记录代码演进。

基线快照是"代码一步步演进"的实测记录，**允许修订**：同一次跑批的快照可按需更新内容，
``make evals-archive-check`` 会把登记 md5 同步为当前文件内容，不会因为修订而报错。
评测中心的 active 基线用于门禁对比；本目录快照用于追溯历史水位与复盘。

用法::

    python scripts/archive_baseline.py archive   # 归档/更新当前 baseline.json（默认）
    python scripts/archive_baseline.py check     # 校验快照可读并把登记 md5 同步为当前内容
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

HEADER = """# 基线快照（可修订）

本目录存放每次全量跑批的基线数据，用于记录代码逐步演进；**允许修订快照内容**。
要更新记录：跑 `make evals-baseline` 生成/更新 `evals/baseline.json`，再执行
`make evals-archive` 写入或更新对应快照；修订后的 md5 由 `make evals-archive-check` 同步登记。

文件名 = 该次跑批的开跑时间（`%Y%m%d-%H%M%S`），与 Phoenix experiment 名
`eval-<开跑时间>-<git>` 对齐，横向对比时不用做映射。

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
    """门禁摘要：硬底线是否过、目标线差哪几项。纯读报告，不调 LLM。"""
    six = report.get("six_metrics") or {}
    faith = (six.get("faithfulness") or {}).get("micro")
    relev = (six.get("answer_accuracy") or {}).get("relevant_rate")
    recall = six.get("recall@k") or {}
    recall8 = recall.get("8")
    parts = []
    if faith is not None:
        parts.append(f"faith {faith:.3f}{'✅' if faith >= 0.85 else '⛔'}")
    if relev is not None:
        parts.append(f"rel {relev:.3f}{'✅' if relev >= 0.80 else '⚠'}")
    if recall8 is not None:
        parts.append(f"recall@8 {recall8:.3f}{'✅' if recall8 >= 0.80 else '⚠'}")
    return " / ".join(parts) or "—"


def read_rows() -> list[list[str]]:
    """读取归档登记表中的快照行（按表格列拆分）。"""
    if not REGISTER.is_file():
        return []
    rows = []
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cols = [col.strip().strip("`") for col in line.strip().strip("|").split("|")]
        if cols:
            rows.append(cols)
    return rows


def write_rows(rows: list[list[str]]) -> None:
    """重写归档登记表；快照行按文件名排序，保持原有 Markdown 反引号格式。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda cols: cols[0])
    body = ""
    for cols in ordered:
        report = cols[3] if len(cols) > 3 and cols[3] != "—" else ""
        report_cell = f"`{report}`" if report else "—"
        body += (f"| `{cols[0]}` | {cols[1]} | {cols[2]} | {report_cell} | "
                 f"`{cols[4]}` | {cols[5]} |\n")
    REGISTER.write_text(HEADER + body, encoding="utf-8")


def cmd_archive(report_name: str = "") -> int:
    """写入或更新当前基线快照，并同步登记表。"""
    if not BASELINE.is_file():
        sys.exit(f"没有基线文件：{BASELINE}\n先跑 `make evals-baseline`。")
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    name = f"{snapshot_key(report)}.json"
    target = ARCHIVE_DIR / name
    created = not target.exists()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASELINE, target)
    target.chmod(0o644)
    digest = md5_of(target)
    rows = [cols for cols in read_rows() if cols[0] != name]
    rows.append([
        name, report.get("at") or "—", report.get("git") or "—",
        report_name or "—", digest, gate_summary(report),
    ])
    write_rows(rows)
    print(f"{'已新增' if created else '已更新'}快照：{target}")
    print(f"md5    ：{digest}")
    print(f"登记   ：{REGISTER}")
    return 0


def cmd_check() -> int:
    """校验快照可读并将登记 md5 同步为当前内容（允许修订）。"""
    if not REGISTER.is_file():
        sys.exit(f"没有归档登记：{REGISTER}")
    rows = read_rows()
    if not rows:
        print("归档登记里暂无快照记录")
        return 0
    bad = []
    changed = []
    for cols in rows:
        name = cols[0]
        path = ARCHIVE_DIR / name
        if not path.is_file():
            bad.append(f"{name}：文件缺失")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            bad.append(f"{name}：不是合法 JSON")
            continue
        digest = md5_of(path)
        if len(cols) > 4 and cols[4] != digest:
            changed.append(f"{name}：md5 已由 {cols[4][:8]}… 同步为 {digest[:8]}…")
            cols[4] = digest
    if changed:
        write_rows(rows)
    for line in (f"{cols[0]} · {cols[1]} · git {cols[2]} · md5 {cols[4]}" for cols in rows):
        print(line)
    for item in changed:
        print("已同步修订：", item)
    if bad:
        print("\n⛔ 快照校验失败：")
        for item in bad:
            print("  -", item)
        return 1
    print(f"\n✅ {len(rows)} 份快照可读；登记 md5 已与当前内容一致（允许修订）")
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