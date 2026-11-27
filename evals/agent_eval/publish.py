"""将受信运行器的 Agent JSON 报告归档到评测中心，账号归属由执行导入的运维人员指定。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, ValidationError

from taozhi.domain.research import ALL_RESEARCH_TOOLS
from evals.center.publish import apply_dataset_fields
from .cases import AGENT_BENCHMARK_CASES
from .contracts import REPORT_SCHEMA_VERSION, expectation_notes
from .reporting import FAILURE_LABELS, TOOL_OUTCOMES, TOOL_PARAMETERS, fingerprint

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]
DATASET_NAME = "agent_research"
SUPPORTED_SCHEMA_VERSIONS = (3, REPORT_SCHEMA_VERSION)
Status = Literal["passed", "failed", "unassessable"]


class Issue(BaseModel):
    """报告中的一条判定问题明细。"""
    failure_type: str
    tool_name: str | None = None
    step: Count | None = None


class Verdict(BaseModel):
    """单条判定结论：判定器、维度、状态与问题清单。"""
    judge: Literal["Observation", "ExecutionStatusJudge", "ToolExecutionJudge", "ReferenceFactJudge",
                   "ToolBoundaryJudge", "DeliverableJudge", "SteeringJudge", "AntiHallucinationJudge",
                   "BudgetJudge", "QuickAnswerJudge", "CoordinatorToolJudge"]
    dimension: Literal["outcome", "trajectory", "governance"]
    status: Status
    failure_type: str | None = None
    issues: list[Issue] = Field(default_factory=list)


class Annotation(BaseModel):
    """工具参数核对标注。"""
    tool_name: str
    keys: list[str]


class Step(BaseModel):
    """工具轨迹中的单步调用记录。"""
    tool_name: str
    status: str | None = None
    step: Count | None = None
    data_count: Count | None = None
    dispatched: StrictBool | None = None
    parameters_match: StrictBool | None = None
    parameter_keys: list[str] | None = None


class CaseRecord(BaseModel):
    """报告中的单条用例结果。"""
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
    """报告的门禁结论。"""
    passed: StrictBool


class Baseline(BaseModel):
    """报告引用的基线摘要。"""
    baseline_sha256: Digest


class Ratio(BaseModel):
    """分子与分母型比率指标。"""
    numerator: Count
    denominator: Count


class Tokens(BaseModel):
    """Token 使用与调用覆盖率统计。"""
    call_coverage_among_known: Ratio
    measured_prompt_tokens: Count | None
    measured_completion_tokens: Count | None


class Metrics(BaseModel):
    """报告的汇总指标。"""
    selected: Count
    passed: Count
    failed: Count
    unassessable: Count
    required_tool_coverage: Ratio
    parameter_accuracy: Ratio
    reference_fact_coverage: Ratio
    token_usage: Tokens
    latency_p50_seconds: float | None = None
    latency_p95_seconds: float | None = None


class AgentReport(BaseModel):
    """受信运行器产出的 Agent 评测报告结构。"""
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
    """把未登记的工具名归一为 unknown。

    Args:
        name: 原始工具名。

    Returns:
        登记过的工具名，或 unknown。
    """
    return name if name in ALL_RESEARCH_TOOLS else "unknown"


def _expected(row: CaseRecord, definition: dict) -> list[str]:
    """把用例期望展开为可展示的预期说明列表。

    Args:
        row: 报告的逐例记录。
        definition: 仓库中的用例定义。

    Returns:
        预期说明列表。
    """
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
    """把报告判定投影为入库逐例结论字典。

    Args:
        check: 报告中的单条判定。

    Returns:
        入库用判定字典。
    """
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


def judge_summary(case_results: list) -> list[dict]:
    """按判定器与维度汇总逐例结论，作为批次级指标入库。

    Args:
        case_results: 报告中通过校验的逐例结果。

    Returns:
        每个判定器与维度组合的通过、失败与不可判定计数。
    """
    buckets: dict[tuple[str, str], dict] = {}
    for row in case_results:
        for verdict in row.verdicts:
            key = (verdict.judge, verdict.dimension)
            bucket = buckets.setdefault(key, {"judge": verdict.judge, "dimension": verdict.dimension,
                                              "passed": 0, "failed": 0, "unassessable": 0})
            bucket[verdict.status] += 1
    return [buckets[key] for key in sorted(buckets)]


def _milliseconds(seconds: float | None) -> int | None:
    """把报告中的秒级时延换算成毫秒整数。"""
    return None if seconds is None else int(round(float(seconds) * 1000))


def _dataset_projection(raw: dict) -> tuple[dict, bool]:
    """解析报告对应的数据集版本：内嵌描述符优先，其次按内容摘要反查，最后归历史版本。"""
    from evals.center import datasets

    block = raw.get("dataset")
    if isinstance(block, dict) and block.get("version") and block.get("suite") == DATASET_NAME:
        version = str(block["version"])
        fields = datasets.dataset_fields(version, DATASET_NAME)
        expected = fields["content_sha256"]
        embedded = block.get("content_sha256")
        if embedded and expected and embedded != expected:
            raise ValueError(
                f"报告内嵌数据集摘要与 {version} 描述符不一致：期望 {expected} 实际 {embedded}")
        return fields, True
    version = datasets.version_for_content_sha(DATASET_NAME, raw.get("dataset_sha256") or "")
    if version:
        return datasets.dataset_fields(version, DATASET_NAME), True
    return datasets.legacy_dataset_fields("agent", DATASET_NAME, raw.get("git_sha"),
                                          source=raw.get("mode") or "live"), False


def project_report(raw: dict) -> tuple[dict, list[dict]]:
    """校验汇总与明细，对所有可展示字段作投影，避免原始运行数据入库。

    Args:
        raw: 运行器产出的报告对象。

    Returns:
        (批次字段, 逐例判定列表)；批次字段包含数据集版本归属与可比性标记。
    """
    report = AgentReport.model_validate(raw)
    if report.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"评测报告版本无效：{report.schema_version}")
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
    dataset, comparable = _dataset_projection(raw)
    metrics = report.metrics.token_usage.model_dump(exclude={"call_coverage_among_known"})
    provenance = report.model_dump(include={"transcript_sha256", "counterexamples_sha256", "model_config_sha256"},
                                   exclude_none=True)
    provenance["comparable"] = bool(comparable)
    metrics["provenance"] = provenance
    ratios = {key: getattr(report.metrics, key) for key in ("required_tool_coverage", "parameter_accuracy", "reference_fact_coverage")}
    ratios["call_coverage_among_known"] = report.metrics.token_usage.call_coverage_among_known
    for key, ratio in ratios.items():
        if ratio.numerator > ratio.denominator:
            raise ValueError("指标分子不能超过可判定分母")
        metrics[key] = {**ratio.model_dump(), "rate": round(ratio.numerator / ratio.denominator, 4) if ratio.denominator else None}
    scores = [row.score for row in report.case_results if row.score is not None]
    usage = report.metrics.token_usage
    prompt_tokens = usage.measured_prompt_tokens
    completion_tokens = usage.measured_completion_tokens
    total_tokens = (
        None if prompt_tokens is None and completion_tokens is None else (prompt_tokens or 0) + (completion_tokens or 0)
    )
    selected = len(report.case_results)
    scope = raw.get("scope") if raw.get("scope") in ("full", "quick", "retrieval", "adversarial", "behavior") else "full"
    run = {
        "run_id": report.run_id, "created_at": created_at, "mode": report.mode, "git_sha": report.git_sha,
        "eval_kind": "agent",
        "report_sha256": fingerprint(raw), "dataset_sha256": report.dataset_sha256, "judges_sha256": report.judges_sha256,
        "baseline_sha256": report.comparison.baseline_sha256 if report.comparison else None, "metrics": metrics,
        "gate_passed": report.gate.passed, **{s + "_cases": counts[s] for s in ("passed", "failed", "unassessable")},
        "schema_version": report.schema_version, "dataset_name": DATASET_NAME,
        "pass_rate": round(counts["passed"] / selected, 4) if selected else None,
        "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
        "latency_p50_ms": _milliseconds(report.metrics.latency_p50_seconds),
        "latency_p95_ms": _milliseconds(report.metrics.latency_p95_seconds),
        "total_tokens": total_tokens, "judges": judge_summary(report.case_results),
        "scope": scope,
    }
    apply_dataset_fields(run, dataset)
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


def publish_report(path: Path, tenant_id: str, owner_id: str, *, mark_baseline: bool = False,
                   profile_id: str | None = None) -> bool:
    """把 Agent 报告归档进评测中心，返回是否新建批次。

    Args:
        path: 报告文件路径。
        tenant_id: 租户编号。
        owner_id: 批次发起人编号。
        mark_baseline: 是否在入库成功后锁定为基线。
        profile_id: 显式指定的算法版本编号。

    Returns:
        新建批次返回 True，已存在返回 False。
    """
    from evals.center import publish as center_publish

    result = center_publish.publish_report(
        Path(path), tenant_id=tenant_id, owner_id=owner_id, kind="agent",
        profile_id=profile_id, mark_baseline=mark_baseline,
    )
    return bool(result["created"])


def resolve_owner(tenant_code: str, owner_username: str) -> tuple[str, str]:
    """按租户编码与账号名解析入库归属。

    Args:
        tenant_code: 企业租户编码。
        owner_username: 批次发起人的登录名。

    Returns:
        (租户编号, 发起人编号)。
    """
    from evals.center.publish import resolve_owner as resolve

    return resolve(tenant_code, owner_username)


def pending_reports(reports_dir: Path) -> list[Path]:
    """列出目录下尚未归档的批次报告，按时间升序。"""
    return sorted(path for path in Path(reports_dir).glob("agent_eval_report_*.json") if path.is_file())


def main() -> None:
    """命令行入口：把 Agent 报告归档进评测中心。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--reports-dir", type=Path, help="批量导入目录，默认 evals/reports/agent_eval")
    parser.add_argument("--tenant-id")
    parser.add_argument("--owner-id")
    parser.add_argument("--tenant-code", help="按租户编码解析归属，与 --owner-username 一起使用")
    parser.add_argument("--owner-username", default="owner")
    parser.add_argument("--profile-id", help="显式指定算法版本编号")
    parser.add_argument("--baseline", action="store_true", help="入库后锁定为基线")
    args = parser.parse_args()
    if args.report is None and args.reports_dir is None:
        parser.error("请指定报告文件或 --reports-dir")
    try:
        if args.tenant_id and args.owner_id:
            tenant_id, owner_id = args.tenant_id, args.owner_id
        else:
            tenant_id, owner_id = resolve_owner(
                args.tenant_code or __import__("os").environ.get("ECOM_TENANT_CODE", "mofa"),
                args.owner_username)
        paths = [args.report] if args.report is not None else pending_reports(args.reports_dir)
    except ValueError as exc:
        parser.exit(2, f"归属解析失败（{exc}）\n")
    created = duplicated = skipped = failed = 0
    for path in paths:
        try:
            if publish_report(path, tenant_id, owner_id, mark_baseline=args.baseline,
                              profile_id=args.profile_id):
                created += 1
                print(f"已入库 {path.name}")
            else:
                duplicated += 1
        except ValidationError:
            skipped += 1
            print(f"跳过字段不兼容的历史报告 {path.name}")
        except ValueError as exc:
            if "版本" in str(exc):
                skipped += 1
                print(f"跳过旧版本报告 {path.name}")
            else:
                failed += 1
                print(f"导入失败 {path.name}（{exc}）")
        except OSError:
            failed += 1
            print(f"读取失败 {path.name}")
    print(f"入库完成：新增 {created}，已存在 {duplicated}，旧版本跳过 {skipped}，失败 {failed}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()