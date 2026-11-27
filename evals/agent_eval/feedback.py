"""从工作台已授权导出的活动页归集故障，仅输出枚举代码与计数。"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Iterable

from taozhi.domain.trace_diagnostic import CATALOG, OUTCOMES


def collect_candidates(pages: Iterable[dict]) -> dict:
    """按诊断及处理结果归组，同一 span 在重叠活动页内只计一次。"""
    spans = set()
    traces = set()
    signals = defaultdict(set)
    unknown = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise ValueError("需要轨迹 API 返回的活动页")
        for activity in page["items"]:
            if not isinstance(activity, dict):
                raise ValueError("活动条目格式无效")
            trace, span = activity.get("trace_id"), activity.get("span_id")
            events = activity.get("diagnostics")
            if not all(isinstance(x, str) and x for x in (trace, span)) or not isinstance(events, list):
                raise ValueError("活动条目缺少链路、节点 ID 或诊断数组")
            traces.add(trace)
            spans.add((trace, span))
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError("诊断条目格式无效")
                code, outcome = event.get("code"), event.get("outcome")
                if not isinstance(code, str) or not isinstance(outcome, str) or code not in CATALOG or outcome not in OUTCOMES:
                    unknown.add((trace, span))
                    continue
                signals[(code, outcome)].add((trace, span))
    candidates = []
    for (code, outcome), affected in sorted(signals.items()):
        candidates.append({
            "category": CATALOG[code][0],
            "code": code,
            "outcome": outcome,
            "affected_traces": len({trace for trace, _ in affected}),
            "affected_spans": len(affected),
        })
    candidates.sort(key=lambda row: -row["affected_traces"])
    return {
        "observed_traces": len(traces),
        "observed_spans": len(spans),
        "unclassified_spans": len(unknown),
        "candidates": candidates,
    }


def main() -> None:
    """命令行入口：汇总同租户活动页中的反馈候选并输出 JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", type=Path, nargs="+", help="从同一租户授权导出的轨迹活动页 JSON 文件")
    args = parser.parse_args()
    try:
        summary = collect_candidates(json.loads(path.read_text(encoding="utf-8")) for path in args.pages)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法生成候选摘要（{type(exc).__name__}），请检查活动页格式。\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
