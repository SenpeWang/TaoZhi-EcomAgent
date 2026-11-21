"""将受信运行器的 JSON 报告归档到工作台，账号归属由执行导入的运维人员指定。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, ValidationError

from taozhi.domain.research import ALL_RESEARCH_TOOLS
from .cases import AGENT_BENCHMARK_CASES
from .reporting import FAILURE_LABELS, TOOL_OUTCOMES, TOOL_PARAMETERS, fingerprint


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Status = Literal["passed", "failed", "unassessable"]
KNOWN_JUDGES = {
    "Observation", "ExecutionStatusJudge", "ToolExecutionJudge", "ReferenceFactJudge", "ToolBoundaryJudge",
    "DeliverableJudge", "SteeringJudge", "AntiHallucinationJudge",
}


class Issue(BaseModel):
    failure_type: str
    tool_name: str | None = None
    step: Count | None = None


class Verdict(BaseModel):
    judge: str
    dimension: Literal["outcome", "trajectory", "governance"]
    status: Status
    failure_type: str | None = None
    issues: list[Issue] = Field(default_factory=list)


class Annotation(BaseModel):
    tool_name: str
    keys: list[str]


class Step(BaseModel):
    tool_name: str
    status: str | None = None
    step: Count | None = None
    data_count: Count | None = None
    dispatched: StrictBool | None = None
    parameters_match: StrictBool | None = None
    parameter_keys: list[str] | None = None


class CaseRecord(BaseModel):
    case_id: Identifier
    case_sha256: Digest
    suite: Literal["benchmark", "golden", "regression", "challenge"]
    status: Status
    score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    hard_failure: StrictBool
    trace_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None = None
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    expected_parameters: list[Annotation] | None = None
    steps: list[Step] | None = None
    steps_complete: StrictBool | None = None
    verdicts: list[Verdict]


class Gate(BaseModel):
    passed: StrictBool


class Baseline(BaseModel):
    baseline_sha256: Digest


class Ratio(BaseModel):
    numerator: Count
    denominator: Count


class Tokens(BaseModel):
    call_coverage_among_known: Ratio | None = None
    measured_prompt_tokens: Count | None = None
    measured_completion_tokens: Count | None = None


class Metrics(BaseModel):
    required_tool_coverage: Ratio | None = None
    parameter_accuracy: Ratio | None = None
    reference_fact_coverage: Ratio | None = None
    token_usage: Tokens = Field(default_factory=Tokens)


class AgentReport(BaseModel):
    schema_version: Literal[2]
    run_id: Identifier
    timestamp: str
    mode: Literal["live", "synthetic_trace_regrade"]
    git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{7,64}$")] | None = None
    dataset_sha256: Digest
    judges_sha256: Digest
    gate: Gate
    comparison: Baseline | None = None
    metrics: Metrics
    total_cases: Count
    passed_cases: Count
    case_results: list[CaseRecord] = Field(min_length=1)


def _tool(name: str) -> str:
    return name if name in ALL_RESEARCH_TOOLS else "unknown"


def _expected(row: CaseRecord, definition: dict) -> list[str] | None:
    if row.expected_tools is None and row.forbidden_tools is None and row.expected_parameters is None:
        return None
    expected = [f"必须调用 {_tool(tool)}" for tool in row.expected_tools or ()]
    expected.extend(f"禁止派发 {_tool(tool)}" for tool in row.forbidden_tools or ())
    for rule in row.expected_parameters or ():
        keys = sorted(TOOL_PARAMETERS.intersection(rule.keys))
        if keys:
            expected.append(f"{_tool(rule.tool_name)} 需核对参数 {'、'.join(keys)}")
    for fact in definition.get("reference_facts", ()):
        expected.append(f"参考事实：{' / '.join(fact['any_of'])}")
    for rule in definition.get("tool_expectations", ()):
        name = f"可选调用 {rule['tool_name']}" if rule.get("optional") else rule["tool_name"]
        for key, constraint in rule.get("parameters", {}).items():
            if isinstance(constraint, dict) and "contains" in constraint:
                expected.append(f"{name} 的 {key} 需包含 {constraint['contains']}")
            elif isinstance(constraint, dict) and "identifier" in constraint:
                expected.append(f"{name} 的 {key} 需包含完整型号 {constraint['identifier']}")
            else:
                expected.append(f"{name} 的 {key} 应为 {constraint}")
        if rule.get("status") in TOOL_OUTCOMES:
            expected.append(f"{name} 的预期状态为 {rule['status']}")
    for section in definition.get("required_sections", ()):
        expected.append(f"交付报告涵盖{section}")
    return expected


def _verdict(check: Verdict) -> dict:
    issues = [{
        "failure_type": issue.failure_type if issue.failure_type in FAILURE_LABELS else "unclassified",
        "tool_name": _tool(issue.tool_name) if issue.tool_name is not None else None,
        "step": issue.step,
    } for issue in check.issues]
    return {
        "judge": check.judge if check.judge in KNOWN_JUDGES else "OtherJudge",
        "dimension": check.dimension, "status": check.status,
        "reason": FAILURE_LABELS.get(check.failure_type, "判定通过" if check.status == "passed" else "判定未通过"),
        "issues": issues,
    }


def project_report(raw: dict) -> tuple[dict, list[dict]]:
    """校验汇总与明细，对所有可展示字段作投影，避免原始运行数据入库。"""
    report = AgentReport.model_validate(raw)
    counts = Counter(row.status for row in report.case_results)
    ids = {row.case_id for row in report.case_results}
    if len(ids) != len(report.case_results) or len(ids) != report.total_cases or counts["passed"] != report.passed_cases:
        raise ValueError("报告的用例数、唯一编号或通过数不一致")
    created_at = datetime.strptime(report.timestamp, "%Y%m%d_%H%M%S_%f").replace(tzinfo=timezone.utc)
    metrics = report.metrics.token_usage.model_dump(exclude={"call_coverage_among_known"})
    ratios = {key: getattr(report.metrics, key) for key in ("required_tool_coverage", "parameter_accuracy", "reference_fact_coverage")}
    ratios["call_coverage_among_known"] = report.metrics.token_usage.call_coverage_among_known
    for key, ratio in ratios.items():
        if ratio and ratio.numerator > ratio.denominator:
            raise ValueError("指标分子不能超过可判定分母")
        metrics[key] = {**ratio.model_dump(), "rate": ratio.numerator / ratio.denominator if ratio.denominator else None} if ratio else None
    run = {
        "run_id": report.run_id, "created_at": created_at, "mode": report.mode, "git_sha": report.git_sha,
        "report_sha256": fingerprint(raw), "dataset_sha256": report.dataset_sha256, "judges_sha256": report.judges_sha256,
        "baseline_sha256": report.comparison.baseline_sha256 if report.comparison else None, "metrics": metrics,
        "gate_passed": report.gate.passed, **{s + "_cases": counts[s] for s in ("passed", "failed", "unassessable")},
    }
    catalog = {(case["id"], fingerprint(case)): case for case in AGENT_BENCHMARK_CASES}
    rows = []
    for row in report.case_results:
        definition = catalog.get((row.case_id, row.case_sha256), {})
        steps = None if row.steps is None else [{
            "tool_name": _tool(step.tool_name), "status": step.status if step.status in TOOL_OUTCOMES else None,
            "step": step.step, "data_count": step.data_count, "dispatched": step.dispatched,
            "parameters_match": step.parameters_match,
            "parameter_keys": sorted(TOOL_PARAMETERS.intersection(step.parameter_keys)) if step.parameter_keys is not None else None,
        } for step in row.steps]
        rows.append({
            "case_id": row.case_id, "case_sha256": row.case_sha256, "name": definition.get("name", row.case_id),
            "category": definition.get("category"), "suite": row.suite, "status": row.status, "score": row.score,
            "hard_failure": row.hard_failure, "trace_id": row.trace_id if report.mode == "live" else None,
            "expected": _expected(row, definition), "observed": steps, "steps_complete": row.steps_complete,
            "verdicts": [_verdict(check) for check in row.verdicts],
        })
    return run, rows


def publish_report(path: Path, tenant_id: str, owner_id: str) -> bool:
    from taozhi.infrastructure.persistence.evaluation_repo import insert_report
    from taozhi.knowledge.db import database_connection

    raw = json.loads(path.read_text(encoding="utf-8"))
    run, rows = project_report(raw)
    with database_connection() as conn:
        return insert_report(conn, tenant_id, owner_id, run, rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--owner-id", required=True)
    args = parser.parse_args()
    try:
        created = publish_report(args.report, args.tenant_id, args.owner_id)
    except ValidationError:
        parser.exit(2, "评测报告的版本或字段无效。\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"报告导入失败（{type(exc).__name__}），请核对文件、租户、发起人与批次内容。\n")
    print("评测批次已入库" if created else "相同批次已存在")


if __name__ == "__main__":
    main()
