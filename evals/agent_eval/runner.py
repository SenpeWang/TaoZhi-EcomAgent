"""可观测多智能体评测 CLI 运行器与报告器（移植自 Piagent cli.ts / report.ts 规范）。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from .cases import AGENT_BENCHMARK_CASES
from .harness import AgentEvalHarness, AgentRunResult
from .judges import (
    AntiHallucinationJudge,
    DeliverableJudge,
    JudgeVerdict,
    SteeringJudge,
    ToolExecutionJudge,
)

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "evals/reports/agent_eval"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def run_agent_evaluations(
    cases: List[Dict[str, Any]],
    sync_phoenix: bool = True,
) -> Dict[str, Any]:
    """执行全量多智能体可观测评测并输出结构化度量报告。"""
    harness = AgentEvalHarness()
    observations: List[Dict[str, Any]] = []
    case_results: List[Dict[str, Any]] = []

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    sha = git_sha()

    print("=" * 80)
    print(f"🤖 淘智真·自治多智能体系统科学评测 (Piagent 规范移植版)")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')} | Git SHA: {sha} | 用例总数: {len(cases)}")
    print("=" * 80)

    total_passed = 0
    total_score = 0.0
    latencies: List[float] = []

    for idx, case in enumerate(cases, start=1):
        case_id = case["id"]
        case_name = case["name"]
        print(f"[{idx}/{len(cases)}] 正在评测: [{case_id}] {case_name} ...")

        # 1. 执行 Harness 沙箱运行
        run_res: AgentRunResult = harness.run(case)
        latencies.append(run_res.elapsed)

        # 2. 执行声明式判定器套件
        verdicts: List[JudgeVerdict] = []

        # 工具调用评测
        tool_judge = ToolExecutionJudge(
            required_tools=case.get("required_tools"),
            forbidden_tools=case.get("forbidden_tools"),
        )
        verdicts.append(tool_judge.evaluate(run_res))

        # 交付工件评测
        deliv_judge = DeliverableJudge(
            required_sections=case.get("required_sections"),
            min_chars=case.get("min_chars", 80),
        )
        verdicts.append(deliv_judge.evaluate(run_res))

        # 真实性与零虚假评测
        anti_fake_judge = AntiHallucinationJudge()
        verdicts.append(anti_fake_judge.evaluate(run_res))

        # 动态 Steering 评测
        if case.get("steering_keyword"):
            steer_judge = SteeringJudge(steering_keyword=case["steering_keyword"])
            verdicts.append(steer_judge.evaluate(run_res))

        # 3. 统计判定结论
        all_passed = all(v.passed for v in verdicts)
        avg_score = round(sum(v.score for v in verdicts) / max(1, len(verdicts)), 2)
        if all_passed:
            total_passed += 1
        total_score += avg_score

        status_tag = "✅ PASS" if all_passed else "❌ FAIL"
        trace_str = f"TraceID: {run_res.trace_id[:16]}..." if run_res.trace_id else "TraceID: N/A"
        print(f"    结论: {status_tag} (得分: {avg_score:.2f}) | 耗时: {run_res.elapsed}s | {trace_str}")
        for v in verdicts:
            v_mark = "✓" if v.passed else "✗"
            print(f"      {v_mark} [{v.judge_name}]: {v.reason}")

        # 记录遥测观察流
        obs = {
            "case_id": case_id,
            "category": case.get("category"),
            "passed": all_passed,
            "score": avg_score,
            "elapsed_seconds": run_res.elapsed,
            "trace_id": run_res.trace_id,
            "tools_called": [t["tool_name"] for t in run_res.tool_calls],
            "verdicts": [
                {"judge": v.judge_name, "passed": v.passed, "score": v.score, "reason": v.reason}
                for v in verdicts
            ],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        observations.append(obs)
        case_results.append({
            "case_id": case_id,
            "name": case_name,
            "passed": all_passed,
            "score": avg_score,
            "elapsed": run_res.elapsed,
            "trace_id": run_res.trace_id,
            "subagents_count": len(run_res.subagents),
            "tool_calls_count": len(run_res.tool_calls),
            "verdicts": obs["verdicts"],
        })

    pass_rate = round(total_passed / max(1, len(cases)), 3)
    mean_score = round(total_score / max(1, len(cases)), 3)
    p50_lat = round(sorted(latencies)[len(latencies) // 2], 2) if latencies else 0.0
    p95_lat = round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0.0

    summary = {
        "git_sha": sha,
        "timestamp": timestamp,
        "total_cases": len(cases),
        "passed_cases": total_passed,
        "pass_rate": pass_rate,
        "mean_score": mean_score,
        "latency_p50_seconds": p50_lat,
        "latency_p95_seconds": p95_lat,
        "case_results": case_results,
    }

    # 落盘写报告与遥测流
    obs_file = REPORTS_DIR / f"agent_observations_{timestamp}.jsonl"
    with open(obs_file, "w", encoding="utf-8") as f:
        for o in observations:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    rep_file = REPORTS_DIR / f"agent_eval_report_{timestamp}.json"
    with open(rep_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))

    print("=" * 80)
    print(f"📊 评测总览：通过率 {pass_rate:.1%} ({total_passed}/{len(cases)}) | 综合得分: {mean_score:.2f}")
    print(f"⏱️ 耗时分布：P50 {p50_lat}s | P95 {p95_lat}s")
    print(f"📁 报告归档：{rep_file.relative_to(ROOT)}")
    print(f"📁 遥测日志：{obs_file.relative_to(ROOT)}")
    print("=" * 80)

    # 联动 Phoenix
    if sync_phoenix:
        try:
            print("🚀 正在将评测遥测同步至 Arize Phoenix (127.0.0.1:6006) ...")
            py_bin = Path(os.path.expanduser("~/.local/share/ecom-agent/phoenix-venv/bin/python"))
            if not py_bin.is_file():
                py_bin = Path(sys.executable)
            subprocess.run([str(py_bin), str(ROOT / "evals/sync_phoenix.py"), "--agent"], check=False)
            print("✓ Phoenix 状态同步完成")
        except Exception as e:
            print(f"⚠️ Phoenix 同步异常: {e}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="多智能体可观测评测 CLI 运行器（测试完成自动同步至 Phoenix）")
    parser.add_argument("--case", default="", help="指定评测单例 ID")
    parser.add_argument("--no-sync", action="store_true", help="跳过自动同步至 Phoenix")
    args = parser.parse_args()

    selected = AGENT_BENCHMARK_CASES
    if args.case:
        selected = [c for c in AGENT_BENCHMARK_CASES if c["id"] == args.case]
        if not selected:
            print(f"错误：未找到 ID 为 '{args.case}' 的测试用例", file=sys.stderr)
            sys.exit(1)

    # 默认自动同步至 Phoenix
    res = run_agent_evaluations(selected, sync_phoenix=not args.no_sync)
    if res["pass_rate"] < 0.75:
        sys.exit(1)


if __name__ == "__main__":
    main()
