"""运行调研评测或重评合成执行记录，生成分层报告和回归门禁结果。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean
import subprocess
import sys
from uuid import uuid4

from taozhi.domain.evaluation_comparison import pair_case_results

from .cases import AGENT_BENCHMARK_CASES
from .contracts import REPORT_SCHEMA_VERSION, validate_cases
from .harness import AgentEvalHarness, replay_transcript
from .judges import evaluate_case
from .reporting import case_record, fingerprint, group_metrics, release_gate, validate_baseline

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "evals/reports/agent_eval"


def git_sha() -> str | None:
    """在可读取 Git 元数据时记录当前提交。"""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def read_synthetic_replay(path: Path, known_ids: set[str]) -> tuple[dict, dict]:
    """拒绝未标记为合成、版本不匹配、重复或未知用例的执行记录。"""
    payload = path.read_bytes()
    envelope = json.loads(payload)
    if not isinstance(envelope, dict) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1:
        raise ValueError("回放文件须使用 schema_version 1")
    if envelope.get("source") != "synthetic" or not isinstance(envelope.get("observations"), list):
        raise ValueError("回放文件须声明合成来源并包含 observations 列表")
    observations = {}
    for observation in envelope["observations"]:
        case_id = observation.get("case_id") if isinstance(observation, dict) else None
        if not isinstance(case_id, str) or case_id not in known_ids or case_id in observations:
            raise ValueError("回放记录含未知或重复的用例编号")
        observations[case_id] = observation
    config = envelope.get("model_config")
    return observations, {
        "transcript_sha256": hashlib.sha256(payload).hexdigest(),
        "model_config_sha256": fingerprint(config) if config is not None else None,
    }


def run_agent_evaluations(cases: list[dict], *,
                          replay_file: Path | None = None, reports_dir: Path | None = None,
                          catalog: list[dict] | None = None, compare_file: Path | None = None,
                          counterexamples_file: Path | None = None, publish_to: tuple[str, str] | None = None,
                          log_path: Path | None = None) -> dict:
    """通过同一组判定器评估实际运行或已有记录，将来源写入独立报告。"""
    dataset = cases if catalog is None else catalog
    validate_cases(dataset)
    if not cases:
        raise ValueError("未选中用例")
    indices = {case["id"]: case for case in dataset}
    if len({case["id"] for case in cases}) != len(cases) or any(indices.get(c["id"]) != c for c in cases):
        raise ValueError("运行用例包含重复或未登记的定义")
    root = Path(reports_dir or REPORTS_DIR)
    mode = "live" if replay_file is None else "synthetic_trace_regrade"
    if counterexamples_file is not None and replay_file is None:
        raise ValueError("反例变更只能应用到合成回放")
    if replay_file is None and any(case.get("execution_mode") == "synthetic_trace_regrade" for case in cases):
        raise ValueError("所选用例依赖固定合成证据，请指定 --replay")
    judges_sha256 = fingerprint({
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("contracts.py", "harness.py", "judges.py", "reporting.py")
    })
    baseline = None
    if compare_file is not None:
        baseline = json.loads(Path(compare_file).read_bytes())
        validate_baseline(baseline, mode=mode, judges_sha256=judges_sha256)
    captured = {}
    source = {"model_config_sha256": None}
    if replay_file is None:
        harness = AgentEvalHarness()
    else:
        captured, source = read_synthetic_replay(Path(replay_file), set(indices))
        if counterexamples_file is not None:
            from .counterexamples import apply_counterexample

            payload = Path(counterexamples_file).read_bytes()
            envelope = json.loads(payload)
            if (not isinstance(envelope, dict) or envelope.get("source") != "synthetic"
                    or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1
                    or not isinstance(envelope.get("counterexamples"), list)):
                raise ValueError("反例文件格式无效")
            selected_ids = {case["id"] for case in cases}
            variants = set()
            for variant in envelope["counterexamples"]:
                cid = variant.get("case_id") if isinstance(variant, dict) else None
                if not isinstance(cid, str) or cid not in captured or cid in variants:
                    raise ValueError("反例缺少对应的合成观测或用例编号重复")
                variants.add(cid)
                if cid in selected_ids:
                    captured[cid] = apply_counterexample(captured[cid], variant)
            if not selected_ids.issubset(variants):
                raise ValueError("所选用例不全都有对应的反例")
            source["counterexamples_sha256"] = hashlib.sha256(payload).hexdigest()
        root = root / mode

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{timestamp}_{uuid4().hex[:8]}"
    records = []
    for case in cases:
        case_id = case["id"]
        if replay_file is None:
            result = harness.run(case)
        elif case_id in captured:
            try:
                result = replay_transcript(case, captured[case_id])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"用例 {case_id} 的执行记录格式无效") from exc
        else:
            result = None
        checks = evaluate_case(case, result) if result is not None else []
        record = case_record(case, result, checks)
        records.append(record)
        print(f"[{case_id}] {record['status']}")

    metrics = group_metrics(records)
    scores = [r["score"] for r in records if r["score"] is not None]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION, "git_sha": git_sha(), "timestamp": timestamp, "run_id": run_id,
        "mode": mode, "dataset_sha256": fingerprint(sorted(dataset, key=lambda c: c["id"])),
        "judges_sha256": judges_sha256,
        **source, "mean_score": round(mean(scores), 4) if scores else None,
        "metrics": metrics,
        "suites": {suite: group_metrics([r for r in records if r["suite"] == suite])
                   for suite in sorted({r["suite"] for r in records})},
        "gate": release_gate(records), "case_results": records,
    }
    if baseline is not None:
        comparison = {"baseline_sha256": fingerprint(baseline), **pair_case_results(records, baseline["case_results"])}
        report["comparison"] = comparison
        if not comparison["passed"]:
            report["gate"]["passed"] = False
            blockers = report["gate"]["blocking_case_ids"]
            blockers.extend(cid for cid in comparison["changes"]["standard"]["regressed"] if cid not in blockers)

    root.mkdir(parents=True, exist_ok=True)
    report_path = root / f"agent_eval_report_{run_id}.json"
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print(f"门禁{'通过' if report['gate']['passed'] else '未通过'}，评测报告 {report_path}")
    if (reports_dir is None and replay_file is None) or log_path is not None:
        from evals.history_log import append_agent_eval_record

        appended = append_agent_eval_record(report, report_path, log_path=log_path)
        print(f"台账已增量追加: {appended}")
    if publish_to is not None:
        from .publish import publish_report

        publish_report(report_path, *publish_to)
        print(f"评测中心已收录批次 {run_id}")
    return report


def main(argv: list[str] | None = None) -> int:
    """缺少记录、硬失败和统计回归使用非零退出码。"""
    parser = argparse.ArgumentParser(description="调研 Agent 评测与合成轨迹重评")
    parser.add_argument("--case", help="只评估指定用例，缺少其录制会记为未判定")
    parser.add_argument("--dataset", choices=["synthetic"], help="只运行固定合成证据用例，须同时提供 --replay")
    parser.add_argument("--replay", type=Path, metavar="PATH", help="读取合成 JSON 执行记录进行重评")
    parser.add_argument("--compare", type=Path, metavar="REPORT", help="与同来源、同裁判的已有 Agent 报告逐例比较")
    parser.add_argument("--counterexamples", type=Path, metavar="PATH", help="在合成回放上重现指定反例，每次只改一项观测")
    parser.add_argument("--publish", nargs=2, metavar=("TENANT_ID", "OWNER_ID"), help="在已配置的工作台数据库归档本次结果")
    parser.add_argument("--output-dir", type=Path, help="报告目录，合成重评另存于其子目录")
    args = parser.parse_args(argv)
    if args.dataset and args.replay is None:
        parser.error("合成数据集需要 --replay 执行记录")
    selected = AGENT_BENCHMARK_CASES
    if args.case:
        selected = [case for case in selected if case["id"] == args.case]
    if args.dataset:
        selected = [case for case in selected if case.get("execution_mode") == "synthetic_trace_regrade"]
    elif args.replay is None and args.case is None:
        selected = [case for case in selected if case.get("execution_mode") != "synthetic_trace_regrade"]
    if not selected:
        parser.error("未找到指定用例")
    try:
        report = run_agent_evaluations(selected, replay_file=args.replay,
                                       reports_dir=args.output_dir, catalog=AGENT_BENCHMARK_CASES,
                                       compare_file=args.compare, counterexamples_file=args.counterexamples,
                                       publish_to=tuple(args.publish) if args.publish else None)
    except (OSError, UnicodeError, json.JSONDecodeError):
        parser.error("无法读取评测文件或写入报告")
    except ValueError as exc:
        parser.error(str(exc))
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
