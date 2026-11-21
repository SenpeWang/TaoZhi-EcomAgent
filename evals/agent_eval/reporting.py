"""Agent 评测口径与可入库审阅的最小失败记录。"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from statistics import mean, median

from taozhi.domain.research import ALL_RESEARCH_TOOLS, StopReason

from .harness import AgentRunResult
from .judges import JudgeVerdict

FAILURE_LABELS = {
    "missing_observation": "缺少可判定的观测",
    "run_status": "执行未按预期结束",
    "tool_forbidden": "实际调用了禁用工具",
    "tool_missing": "缺少必调工具",
    "tool_arguments": "工具入参未满足契约",
    "tool_status": "工具返回状态与预期不符",
    "tool_data": "工具数据量与预期不符",
    "tool_order": "工具调用依赖顺序不符",
    "deliverable_structure": "交付内容缺少约定结构",
    "fabricated_trace": "报告包含模拟执行文案",
    "steering": "运行期干预未满足约定",
    "reference_fact": "报告缺少参考事实",
    "evidence_citation": "参考事实缺少对应证据",
    "forbidden_claim": "报告出现标注禁止的结论",
}

COUNTERS = (
    "required_tool_hits", "required_tool_total", "required_tool_unknown", "parameter_hits", "parameter_total",
    "parameter_unknown", "selection_hits", "selection_total", "selection_unknown",
    "forbidden_executions", "forbidden_attempts", "fact_hits", "fact_total", "fact_unknown",
)


def fingerprint(value: object) -> str:
    """对用例及运行配置求稳定摘要，保留列表中具有语义的顺序。"""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator, "rate": _rate(numerator, denominator)}


def _public_verdict(verdict: JudgeVerdict) -> dict:
    issues = []
    for issue in verdict.details.get("issues", ()):
        failure = issue.get("failure_type")
        step = issue.get("step")
        tool = issue.get("tool_name")
        issues.append({
            "failure_type": failure if failure in FAILURE_LABELS else "unclassified",
            "step": step if type(step) is int and step >= 0 else None,
            "tool_name": tool if tool in ALL_RESEARCH_TOOLS else None,
        })
    status = verdict.status
    failure = verdict.failure_type
    return {
        "judge": verdict.judge_name, "dimension": verdict.dimension, "status": status,
        "passed": status == "passed", "score": verdict.score if status != "unassessable" else None,
        "failure_type": failure, "hard_failure": verdict.hard_failure,
        "reason": FAILURE_LABELS.get(failure, "判定通过" if status == "passed" else "判定未通过"),
        "issues": issues,
        "counts": {key: verdict.details[key] for key in COUNTERS if key in verdict.details},
    }


def case_record(case: dict, result: AgentRunResult | None, verdicts: list[JudgeVerdict]) -> dict:
    """仅保留受控分类、数值与定位信息，线上内容不进入报告。"""
    checks = [_public_verdict(v) for v in verdicts]
    if result is None:
        checks.append({
            "judge": "Observation", "dimension": "trajectory", "status": "unassessable",
            "passed": False, "score": None, "failure_type": "missing_observation",
            "reason": FAILURE_LABELS["missing_observation"], "hard_failure": False, "issues": [], "counts": {},
        })
    statuses = {v["status"] for v in checks}
    status = "passed"
    if "failed" in statuses:
        status = "failed"
    elif "unassessable" in statuses or not checks:
        status = "unassessable"
    scores = [v["score"] for v in checks if v["score"] is not None]
    trace = result.trace_id if result else None
    usage = result.token_usage if result else {}
    models = result.model_names if result else []
    stop = result.stop_reason if result else None
    terminal = result.system_status if result else None
    return {
        "case_id": case["id"], "case_sha256": fingerprint(case), "name": case["name"],
        "category": case.get("category"), "suite": case.get("suite", "benchmark"),
        "status": status, "passed": status == "passed", "score": round(mean(scores), 4) if scores else None,
        "critical": bool(case.get("critical")), "hard_failure": any(v["hard_failure"] for v in checks),
        "observed": result is not None, "elapsed": result.elapsed if result else None,
        "system_status": terminal if terminal in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"} else None,
        "stop_reason": stop if stop in {reason.value for reason in StopReason} else None,
        "observed_models_sha256": fingerprint(sorted(set(models))) if models else None,
        "trace_id": trace if trace and re.fullmatch(r"[0-9a-f]{32}", trace) else None,
        "subagents_count": len(result.subagents) if result else None,
        "tool_calls_count": len(result.tool_calls) if result else None,
        "token_usage": {key: usage.get(key) for key in ("model_calls", "measured_calls", "prompt_tokens", "completion_tokens")},
        "verdicts": checks,
    }


def group_metrics(records: list[dict]) -> dict:
    """分别统计可判定、未判定与未执行，用量只累加已计量的模型调用。"""
    states = Counter(r["status"] for r in records)
    counts = Counter()
    dimensions = {}
    for record in records:
        for verdict in record["verdicts"]:
            counts.update(verdict["counts"])
            dimensions.setdefault(verdict["dimension"], Counter())[verdict["status"]] += 1
    dimension_metrics = {}
    for dimension in ("outcome", "trajectory", "governance"):
        checks = dimensions.get(dimension, {})
        passed, failed = checks.get("passed", 0), checks.get("failed", 0)
        dimension_metrics[dimension] = {**_ratio(passed, passed + failed), "unassessable": checks.get("unassessable", 0)}
    usage = [r["token_usage"] for r in records]
    known = [u for u in usage if u["model_calls"] is not None]
    measured = [u for u in usage if (u["measured_calls"] or 0) > 0]
    elapsed = sorted(r["elapsed"] for r in records if r["elapsed"] is not None)
    return {
        "selected": len(records), "observed": sum(r["observed"] for r in records),
        "passed": states["passed"], "failed": states["failed"], "unassessable": states["unassessable"],
        "task_completion": _ratio(states["passed"], states["passed"] + states["failed"]),
        "dimensions": dimension_metrics,
        "required_tool_coverage": {
            **_ratio(counts["required_tool_hits"], counts["required_tool_total"] - counts["required_tool_unknown"]),
            "unknown": counts["required_tool_unknown"],
        },
        "tool_selection_accuracy": {**_ratio(counts["selection_hits"], counts["selection_total"]), "unknown": counts["selection_unknown"]},
        "parameter_accuracy": {**_ratio(counts["parameter_hits"], counts["parameter_total"]), "unknown": counts["parameter_unknown"]},
        "reference_fact_coverage": {
            **_ratio(counts["fact_hits"], counts["fact_total"] - counts["fact_unknown"]),
            "unknown": counts["fact_unknown"],
        },
        "forbidden_attempts": counts["forbidden_attempts"], "forbidden_executions": counts["forbidden_executions"],
        "token_usage": {
            "cases_with_model_call_count": len(known), "cases_with_token_counts": len(measured),
            "case_coverage": _ratio(len(measured), len(records)),
            "call_coverage_among_known": _ratio(sum(u["measured_calls"] or 0 for u in known), sum(u["model_calls"] for u in known)),
            "measured_prompt_tokens": sum(u["prompt_tokens"] for u in measured) if measured else None,
            "measured_completion_tokens": sum(u["completion_tokens"] for u in measured) if measured else None,
        },
        "latency_observations": len(elapsed),
        "latency_p50_seconds": median(elapsed) if elapsed else None,
        "latency_p95_seconds": elapsed[math.ceil(0.95 * len(elapsed)) - 1] if elapsed else None,
    }


def release_gate(records: list[dict]) -> dict:
    """基准维持原有通过率底线，同时逐例判定硬失败、回归和缺测。"""
    blockers = []
    benchmark = [r for r in records if r["suite"] == "benchmark"]
    for record in records:
        incomplete = any(v["status"] == "unassessable" for v in record["verdicts"])
        required = record["critical"] or record["suite"] in {"regression", "golden"}
        if record["hard_failure"] or (required and record["status"] != "passed") or (incomplete and record["suite"] != "challenge"):
            blockers.append(record["case_id"])
    benchmark_passed = sum(r["passed"] for r in benchmark)
    meets_floor = 4 * benchmark_passed >= 3 * len(benchmark)
    any_observations = any(r["observed"] for r in records)
    return {
        "passed": any_observations and not blockers and meets_floor,
        "blocking_case_ids": blockers, "benchmark_pass_rate": _rate(benchmark_passed, len(benchmark)), "benchmark_minimum": 0.75,
    }


def review_candidates(records: list[dict]) -> list[dict]:
    """为后续人工归因和合成重建保留最小索引，避免复制业务样本。"""
    candidates = []
    for record in records:
        for verdict in record["verdicts"]:
            if verdict["status"] != "passed":
                candidates.append({
                    "case_id": record["case_id"], "case_sha256": record["case_sha256"],
                    "trace_id": record["trace_id"], "judge": verdict["judge"],
                    "failure_type": verdict["failure_type"], "issues": verdict["issues"],
                })
    return candidates


def validate_baseline(baseline: dict, *, schema_version: int, mode: str, judges_sha256: str) -> None:
    """在调用模型前确认对照可用，拒绝旧报告、来源错配与重复样本。"""
    if not isinstance(baseline, dict) or baseline.get("schema_version") != schema_version:
        raise ValueError("对照报告版本不兼容")
    if baseline.get("mode") != mode:
        raise ValueError("对照报告与当前报告的执行来源不同")
    if baseline.get("judges_sha256") != judges_sha256:
        raise ValueError("对照报告使用的判定器版本不同")
    seen = set()
    if not isinstance(baseline.get("case_results"), list):
        raise ValueError("对照报告缺少逐例结果")
    for row in baseline["case_results"]:
        if not isinstance(row, dict):
            raise ValueError("对照报告的逐例结果无效")
        case_id = row.get("case_id")
        checksum = row.get("case_sha256")
        if (not isinstance(case_id, str) or not case_id or case_id in seen
                or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                or row.get("status") not in ("passed", "failed", "unassessable")):
            raise ValueError("对照报告含重复或无效的用例记录")
        seen.add(case_id)


def compare_reports(current: dict, baseline: dict) -> dict:
    """只配对同来源、同裁判、同用例版本的观察，挑战集退化单列。"""
    validate_baseline(baseline, schema_version=current["schema_version"], mode=current["mode"],
                      judges_sha256=current["judges_sha256"])
    previous = {row["case_id"]: row for row in baseline["case_results"]}
    changes = {group: {"stable_passed": [], "stable_failed": [], "fixed": [], "regressed": []}
               for group in ("standard", "challenge")}
    excluded = {"new": [], "baseline_only": [], "definition_changed": [], "unassessable": []}
    records = current["case_results"]
    for row in records:
        case_id = row["case_id"]
        other = previous.pop(case_id, None)
        if other is None:
            excluded["new"].append(case_id)
        elif other["case_sha256"] != row["case_sha256"]:
            excluded["definition_changed"].append(case_id)
        elif "unassessable" in (other["status"], row["status"]):
            excluded["unassessable"].append(case_id)
        else:
            if row["status"] == other["status"]:
                change = "stable_" + row["status"]
            else:
                change = "fixed" if row["status"] == "passed" else "regressed"
            group = "challenge" if row["suite"] == "challenge" else "standard"
            changes[group][change].append(case_id)
    excluded["baseline_only"] = sorted(previous)
    count = {group: sum(len(ids) for ids in results.values()) for group, results in changes.items()}
    comparable = count["standard"] > 0 if any(row["suite"] != "challenge" for row in records) else count["challenge"] > 0
    return {
        "baseline_sha256": fingerprint(baseline), "paired_counts": count, "changes": changes,
        "excluded_count": sum(len(ids) for ids in excluded.values()), "excluded": excluded,
        "comparable": comparable, "passed": comparable and not changes["standard"]["regressed"],
    }
