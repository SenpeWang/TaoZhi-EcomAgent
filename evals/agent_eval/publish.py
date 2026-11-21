"""将受信运行器的 JSON 报告归档到工作台，账号归属由执行导入的运维人员指定。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, ValidationError

from taozhi.domain.research import ALL_RESEARCH_TOOLS
from .cases import AGENT_BENCHMARK_CASES
from .contracts import REPORT_SCHEMA_VERSION, expectation_notes
from .reporting import FAILURE_LABELS, TOOL_OUTCOMES, TOOL_PARAMETERS, fingerprint


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Status = Literal["passed", "failed", "unassessable"]


class Issue(BaseModel):
    failure_type: str
    tool_name: str | None = None
    step: Count | None = None


class Verdict(BaseModel):
    judge: Literal["Observation", "ExecutionStatusJudge", "ToolExecutionJudge", "ReferenceFactJudge",
                   "ToolBoundaryJudge", "DeliverableJudge", "SteeringJudge", "AntiHallucinationJudge"]
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
    expectation_notes: list[str]
    expected_tools: list[str]
    forbidden_tools: list[str]
    expected_parameters: list[Annotation]
    steps: list[Step] | None
    steps_complete: StrictBool | None
    verdicts: list[Verdict]


class Gate(BaseModel):
    passed: StrictBool


class Baseline(BaseModel):
    baseline_sha256: Digest


class Ratio(BaseModel):
    numerator: Count
    denominator: Count


class Tokens(BaseModel):
    call_coverage_among_known: Ratio
    measured_prompt_tokens: Count | None
    measured_completion_tokens: Count | None


class Metrics(BaseModel):
    selected: Count
    passed: Count
    failed: Count
    unassessable: Count
    required_tool_coverage: Ratio
    parameter_accuracy: Ratio
    reference_fact_coverage: Ratio
    token_usage: Tokens


class AgentReport(BaseModel):
    schema_version: StrictInt
    run_id: Identifier
    timestamp: str
    mode: Literal["live", "synthetic_trace_regrade"]
    git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{7,64}$")] | None = None
    dataset_sha256: Digest
    judges_sha256: Digest
    transcript_sha256: Digest | None = None
    counterexamples_sha256: Digest | None = None
    model_config_sha256: Digest | None = None
    gate: Gate
    comparison: Baseline | None = None
    metrics: Metrics
    case_results: list[CaseRecord] = Field(min_length=1)


def _tool(name: str) -> str:
    return name if name in ALL_RESEARCH_TOOLS else "unknown"


def _expected(row: CaseRecord, definition: dict) -> list[str]:
    expected = [f"必须调用 {_tool(tool)}" for tool in row.expected_tools]
    expected.extend(f"禁止派发 {_tool(tool)}" for tool in row.forbidden_tools)
    for rule in row.expected_parameters:
        keys = sorted(TOOL_PARAMETERS.intersection(rule.keys))
        if keys:
            expected.append(f"{_tool(rule.tool_name)} 需核对参数 {'、'.join(keys)}")
    if definition:
        if row.expectation_notes != expectation_notes(definition):
            raise ValueError(f"{row.case_id} 的预期说明与登记的用例版本不符")
        expected.extend(row.expectation_notes)
    return expected


def _verdict(check: Verdict) -> dict:
    issues = [{
        "failure_type": issue.failure_type if issue.failure_type in FAILURE_LABELS else "unclassified",
        "reason": FAILURE_LABELS.get(issue.failure_type, "未分类故障"),
        "tool_name": _tool(issue.tool_name) if issue.tool_name is not None else None,
        "step": issue.step,
    } for issue in check.issues]
    return {
        "judge": check.judge,
        "dimension": check.dimension, "status": check.status,
        "reason": FAILURE_LABELS.get(check.failure_type, "判定通过" if check.status == "passed" else "判定未通过"),
        "issues": issues,
    }


def project_report(raw: dict) -> tuple[dict, list[dict]]:
    """校验汇总与明细，对所有可展示字段作投影，避免原始运行数据入库。"""
    report = AgentReport.model_validate(raw)
    if report.schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError("评测报告版本无效")
    if report.mode == "synthetic_trace_regrade" and not report.transcript_sha256:
        raise ValueError("合成评测缺少回放记录摘要")
    if report.mode == "live" and (report.transcript_sha256 or report.counterexamples_sha256):
        raise ValueError("真实调研报告不能声明合成回放来源")
    counts = Counter(row.status for row in report.case_results)
    ids = {row.case_id for row in report.case_results}
    if len(ids) != len(report.case_results) or len(ids) != report.metrics.selected:
        raise ValueError("报告的用例数或唯一编号不一致")
    if any(counts[status] != getattr(report.metrics, status) for status in ("passed", "failed", "unassessable")):
        raise ValueError("报告的各状态数量与逐例结果不一致")
    created_at = datetime.strptime(report.timestamp, "%Y%m%d_%H%M%S_%f").replace(tzinfo=timezone.utc)
    metrics = report.metrics.token_usage.model_dump(exclude={"call_coverage_among_known"})
    provenance = report.model_dump(include={"transcript_sha256", "counterexamples_sha256", "model_config_sha256"},
                                   exclude_none=True)
    if provenance:
        metrics["provenance"] = provenance
    ratios = {key: getattr(report.metrics, key) for key in ("required_tool_coverage", "parameter_accuracy", "reference_fact_coverage")}
    ratios["call_coverage_among_known"] = report.metrics.token_usage.call_coverage_among_known
    for key, ratio in ratios.items():
        if ratio.numerator > ratio.denominator:
            raise ValueError("指标分子不能超过可判定分母")
        metrics[key] = {**ratio.model_dump(), "rate": round(ratio.numerator / ratio.denominator, 4) if ratio.denominator else None}
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
