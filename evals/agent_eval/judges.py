"""根据实际运行记录判断调研交付、工具轨迹与可引用事实。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from taozhi.domain.research import SOURCE_TOOLS

from .contracts import required_tool_names

if TYPE_CHECKING:
    from .harness import AgentRunResult


@dataclass
class JudgeVerdict:
    """分数衡量已声明契约的履约程度，缺失观测不能计作通过。"""

    judge_name: str
    passed: bool
    score: float
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    dimension: str = "outcome"
    failure_type: str | None = None

    @property
    def status(self) -> str:
        """把判定结果映射为 passed/failed/unassessable。

        Returns:
            状态字符串。
        """
        if self.passed:
            return "passed"
        return "unassessable" if self.failure_type == "missing_observation" else "failed"

    @property
    def hard_failure(self) -> bool:
        """判断是否为工具越权或禁止结论等硬失败。

        Returns:
            是否硬失败。
        """
        return self.failure_type in {"tool_forbidden", "forbidden_claim"}


def _issue(kind: str, reason: str, name: str | None = None, call: dict | None = None) -> dict:
    """构造一条判定问题明细。

    Args:
        kind: 失败类型。
        reason: 问题说明。
        name: 相关工具名。
        call: 相关工具调用记录。

    Returns:
        问题明细字典。
    """
    return {"failure_type": kind, "reason": reason, "tool_name": name, "step": (call or {}).get("sequence")}


def _verdict(name: str, dimension: str, issues: list[dict], success: str, **counts: Any) -> JudgeVerdict:
    """把问题清单汇总为带主因与计数的判定结果。

    Args:
        name: 判定器名称。
        dimension: 判定维度。
        issues: 问题明细列表。
        success: 通过时的说明。
        **counts: 额外的判定计数。

    Returns:
        判定结果对象。
    """
    kinds = [issue["failure_type"] for issue in issues]
    primary = next((kind for kind in kinds if kind in {"tool_forbidden", "forbidden_claim"}), None)
    if primary is None:
        primary = next((kind for kind in kinds if kind != "missing_observation"), next(iter(kinds), None))
    return JudgeVerdict(
        name, not issues, float(not issues), "。".join(issue["reason"] for issue in issues) or success,
        {"issues": issues, **counts}, dimension, primary,
    )


def _arguments_match(parameters: dict | None, constraints: dict) -> bool | None:
    """核对工具调用参数是否满足期望约束。

    Args:
        parameters: 工具调用参数。
        constraints: 参数期望约束。

    Returns:
        满足为 True，不满足为 False，无法判定为 None。
    """
    if parameters is None:
        return None
    for key, expected in constraints.items():
        if key not in parameters:
            return False
        actual = parameters[key]
        if isinstance(expected, dict) and set(expected) == {"contains"}:
            if not isinstance(actual, str) or expected["contains"] not in actual:
                return False
        elif isinstance(expected, dict) and set(expected) == {"identifier"}:
            pattern = r"(?<![A-Za-z0-9_-])" + re.escape(expected["identifier"]) + r"(?![A-Za-z0-9_-])"
            if not isinstance(actual, str) or not re.search(pattern, actual, flags=re.IGNORECASE | re.ASCII):
                return False
        elif actual != expected or type(actual) is not type(expected):
            return False
    return True


def _contract_issues(rule: dict, call: dict) -> list[dict]:
    """全部条件必须由同一次实际调用满足。"""
    name = rule["tool_name"]
    problems = []
    if rule.get("parameters"):
        match = _arguments_match(call.get("parameters"), rule["parameters"])
        if match is not True:
            kind = "tool_arguments" if match is False else "missing_observation"
            problems.append(_issue(kind, f"{name} 的参数未满足标注或未采集", name, call))
    if "status" in rule and call.get("status") != rule["status"]:
        kind = "tool_status" if call.get("status") else "missing_observation"
        problems.append(_issue(kind, f"{name} 未观察到预期状态 {rule['status']}", name, call))
    minimum = rule.get("min_data_count")
    count = call.get("data_count")
    if rule.get("status") == "empty" and call.get("status") == "empty" and count not in (None, 0):
        problems.append(_issue("tool_data", f"{name} 的空结果含非零条数", name, call))
    if minimum is not None and (count is None or count < minimum):
        kind = "tool_data" if count is not None else "missing_observation"
        problems.append(_issue(kind, f"{name} 的结果条数未达到标注下限或未采集", name, call))
    return problems


def matches_any_arguments(parameters: dict | None, alternatives: list[dict]) -> bool | None:
    """对同一工具的多种合法入参做三态判定，供判定与展示共用。"""
    if not alternatives or parameters is None:
        return None
    return any(_arguments_match(parameters, rule) for rule in alternatives)


def _before(first: dict, second: dict) -> bool | None:
    """合并的并行分支记录不提供全局执行顺序。"""
    if type(first.get("sequence")) is int and type(second.get("sequence")) is int:
        return first["sequence"] < second["sequence"]
    return None


class ToolBoundaryJudge:
    """核对任务的工具范围，分别记录已拦截和已实际派发的调用。"""

    def __init__(self, allowed_tools: list[str] | None, forbidden_tools: list[str] | None) -> None:
        """初始化工具越界判定器。

        Args:
            allowed_tools: 允许派发的工具列表。
            forbidden_tools: 禁止派发的工具列表。
        """
        self.allowed = None if allowed_tools is None else set(allowed_tools)
        self.forbidden = set(forbidden_tools or ())

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定实际派发与执行的工具是否越界。

        Args:
            result: 运行结果对象。

        Returns:
            工具越界判定结果。
        """
        hits = total = unknown = attempts = executions = 0
        issues = []
        if not result.tool_trace_complete:
            issues.append(_issue("missing_observation", "工具轨迹采集不完整"))
        for call in result.tool_calls:
            name = call["tool_name"]
            restricted = name in self.forbidden or (self.allowed is not None and name not in self.allowed)
            attempts += int(restricted)
            dispatched = call.get("dispatched")
            if dispatched is None:
                unknown += 1
                if restricted:
                    issues.append(_issue("missing_observation", f"无法确认受限工具 {name} 是否实际执行", name, call))
            elif dispatched:
                total += 1
                if restricted:
                    executions += 1
                    issues.append(_issue("tool_forbidden", f"实际调用了受限工具 {name}", name, call))
                else:
                    hits += 1
        return _verdict("ToolBoundaryJudge", "governance", issues, "实际派发符合标注的工具范围",
                        selection_hits=hits, selection_total=total, selection_unknown=unknown,
                        forbidden_attempts=attempts, forbidden_executions=executions)


class ToolExecutionJudge:
    """校验实际调度、参数、状态、必需来源与有因果依赖的工具顺序。"""

    def __init__(
        self, required_tools: list[str] | None = None, *, min_data_count: int = 1,
        tool_expectations: list[dict] | None = None, tool_order: list[list[str]] | None = None,
    ) -> None:
        """初始化工具执行判定器。

        Args:
            required_tools: 必需工具列表。
            min_data_count: 最少数据条数。
            tool_expectations: 工具期望规则列表。
            tool_order: 工具依赖顺序约束。
        """
        self.rules: dict[str, list[dict]] = {}
        for rule in tool_expectations or ():
            self.rules.setdefault(rule["tool_name"], []).append(rule)
        self.order = tool_order or []
        self.required = required_tool_names(required_tools or [], tool_expectations or [], self.order)
        self.min_data_count = min_data_count

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定必需工具、参数、数据量与依赖顺序是否满足。

        Args:
            result: 运行结果对象。

        Returns:
            工具执行判定结果。
        """
        issues = []
        complete = result.tool_trace_complete
        if not complete:
            issues.append(_issue("missing_observation", "工具轨迹采集不完整"))
        observed: dict[str, list[dict]] = {}
        uncertain = set()
        required_unknown = 0
        parameter_hits = parameter_total = parameter_unknown = 0
        for call in result.tool_calls:
            name = call["tool_name"]
            dispatch = call.get("dispatched")
            if dispatch is None:
                uncertain.add(name)
            if dispatch is not True:
                continue
            observed.setdefault(name, []).append(call)
            constraints = [rule["parameters"] for rule in self.rules.get(name, []) if rule.get("parameters")]
            if constraints:
                match = matches_any_arguments(call.get("parameters"), constraints)
                if match is True:
                    parameter_hits += 1
                    parameter_total += 1
                elif match is None:
                    parameter_unknown += 1
                    issues.append(_issue("missing_observation", f"{name} 未采集调用参数", name, call))
                else:
                    parameter_total += 1
                    issues.append(_issue("tool_arguments", f"{name} 的调用参数与所有标注不符", name, call))

        for name in sorted(self.required | self.rules.keys()):
            calls = observed.get(name, [])
            if not calls:
                if name not in self.required:
                    continue
                kind = "missing_observation" if name in uncertain or not complete else "tool_missing"
                required_unknown += int(kind == "missing_observation")
                issues.append(_issue(kind, f"缺少必须调用的工具执行记录 {name}", name))
                continue
            rules = self.rules.get(name, [])
            for rule in rules:
                applicable = calls
                if rule.get("optional") and rule.get("parameters"):
                    applicable = [call for call in calls if _arguments_match(call.get("parameters"), rule["parameters"])]
                candidates = [_contract_issues(rule, call) for call in applicable]
                if not candidates or not all(candidates):
                    continue
                possible = [errors for errors in candidates if all(e["failure_type"] == "missing_observation" for e in errors)]
                if (name in uncertain or not complete) and not possible:
                    issues.append(_issue("missing_observation", f"{name} 缺少完整执行记录以判定契约", name))
                else:
                    issues.extend(min(possible or candidates, key=len))
            if not rules and self.min_data_count > 0:
                counts = [call.get("data_count") for call in calls if call.get("status") in (None, "ok")]
                total = sum(n for n in counts if n is not None)
                if total < self.min_data_count:
                    kind = "missing_observation" if None in counts or name in uncertain or not complete else "tool_data"
                    issues.append(_issue(kind, f"必须来源 {name} 的有效数据条数不足", name))

        for first, second in self.order:
            for later in observed.get(second, []):
                earlier = observed.get(first, [])
                if not earlier:
                    continue
                precedes = [_before(item, later) for item in earlier]
                if True not in precedes:
                    kind = "missing_observation" if None in precedes or first in uncertain or not complete else "tool_order"
                    issues.append(_issue(kind, f"未证实 {first} 先于 {second} 执行", second, later))

        if not self.required and not self.rules and self.min_data_count > 0:
            counts = [call.get("data_count") for source in SOURCE_TOOLS for call in observed.get(source, ())
                      if call.get("status") in (None, "ok")]
            if sum(n for n in counts if n is not None) < self.min_data_count:
                kind = "missing_observation" if None in counts or uncertain.intersection(SOURCE_TOOLS) or not complete else "tool_data"
                issues.append(_issue(kind, "调研来源的有效数据条数不足"))

        unique_issues = []
        for issue in issues:
            if issue not in unique_issues:
                unique_issues.append(issue)
        verdict = _verdict(
            "ToolExecutionJudge", "trajectory", unique_issues, "工具执行符合本例标注",
            required_tool_hits=len(self.required.intersection(observed)), required_tool_total=len(self.required),
            required_tool_unknown=required_unknown,
            parameter_hits=parameter_hits, parameter_total=parameter_total, parameter_unknown=parameter_unknown,
        )
        if issues and all(issue["failure_type"] == "tool_data" for issue in issues):
            verdict.score = 0.5
        return verdict


class DeliverableJudge:
    """按标注检查交付物长度与报告章节。"""

    def __init__(self, required_sections: list[str] | None = None, min_chars: int = 80) -> None:
        """初始化交付物判定器。

        Args:
            required_sections: 报告必须包含的小节列表。
            min_chars: 最少字符数。
        """
        self.required_sections = ["概况", "供应链", "建议"] if required_sections is None else required_sections
        self.min_chars = min_chars

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定研报长度与关键小节是否达标。

        Args:
            result: 运行结果对象。

        Returns:
            交付物判定结果。
        """
        report = result.report or ""
        issues = []
        if len(report) < self.min_chars:
            issues.append(_issue("deliverable_structure", f"研报字符数不足，需要至少 {self.min_chars} 字"))
        missing = [section for section in self.required_sections if section not in report]
        if missing:
            issues.append(_issue("deliverable_structure", f"研报缺失关键大纲小节 {'、'.join(missing)}"))
        verdict = _verdict("DeliverableJudge", "outcome", issues, "报告满足长度和章节要求",
                           report_chars=len(report), missing_sections=missing)
        if issues and self.required_sections:
            verdict.score = round(0.7 * (1 - len(missing) / len(self.required_sections)), 2)
        return verdict


class AntiHallucinationJudge:
    """匹配已知的伪造轨迹文案，事实依据另按参考标注核对。"""

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定研报是否包含已知的伪造轨迹文案。

        Args:
            result: 运行结果对象。

        Returns:
            反幻觉判定结果。
        """
        markers = ("思考\n总指挥", "运行工具\n规划电商调研大纲", "思考\n全景调研报告编制完成")
        issues = []
        if any(marker in (result.report or "") for marker in markers):
            issues.append(_issue("fabricated_trace", "检测到虚假思考文案"))
        return _verdict("AntiHallucinationJudge", "outcome", issues, "报告未包含已知的伪造轨迹文案")


class BudgetJudge:
    """核对工具调用次数与端到端耗时是否超出用例声明的预算。"""

    def __init__(self, max_tool_calls: int | None = None,
                 max_elapsed_seconds: float | None = None) -> None:
        """初始化预算判定器。

        Args:
            max_tool_calls: 允许的最大工具调用次数。
            max_elapsed_seconds: 允许的最大耗时（秒）。
        """
        self.max_tool_calls = max_tool_calls
        self.max_elapsed_seconds = max_elapsed_seconds

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定工具调用次数与耗时是否超出预算。

        Args:
            result: 运行结果对象。

        Returns:
            预算判定结果。
        """
        issues = []
        calls = [call for call in result.tool_calls if call.get("dispatched") is not False]
        if self.max_tool_calls is not None and len(calls) > self.max_tool_calls:
            issues.append(_issue(
                "budget_exceeded",
                f"工具调用 {len(calls)} 次超出预算 {self.max_tool_calls} 次",
            ))
        elapsed = result.elapsed
        if self.max_elapsed_seconds is not None:
            if elapsed is None:
                issues.append(_issue("missing_observation", "未采集端到端耗时，预算无法判定"))
            elif float(elapsed) > float(self.max_elapsed_seconds):
                issues.append(_issue(
                    "budget_exceeded",
                    f"耗时 {float(elapsed):.1f}s 超出预算 {float(self.max_elapsed_seconds):.1f}s",
                ))
        return _verdict("BudgetJudge", "trajectory", issues, "工具调用次数与耗时均在预算内",
                        tool_calls_total=len(calls), elapsed_seconds=elapsed)


class SteeringJudge:
    """检查干预指令的交付结果，以及有序观测中工具调用是否随指令切换。"""

    def __init__(self, steering_keyword: str, phases: dict[str, dict] | None = None) -> None:
        """初始化动态干预判定器。

        Args:
            steering_keyword: 干预要求关键词。
            phases: 干预前后各阶段的工具契约。
        """
        self.steering_keyword = steering_keyword
        self.phases = phases or {}

    def _boundary_issues(self, result: AgentRunResult) -> list[dict]:
        """校验干预时点前后各阶段工具调用是否符合契约。

        Args:
            result: 运行结果对象。

        Returns:
            问题明细列表。
        """
        boundary = result.steering_after_sequence
        if boundary is None or not result.tool_trace_complete:
            return [_issue("missing_observation", "缺少干预时点或完整的工具轨迹")]
        issues = []
        for phase, rule in self.phases.items():
            matched = False
            uncertain = False
            for call in result.tool_calls:
                if call["tool_name"] != rule["tool_name"] or call.get("dispatched") is False:
                    continue
                same_args = _arguments_match(call.get("parameters"), rule.get("parameters", {}))
                if same_args is False:
                    continue
                sequence = call.get("sequence")
                if same_args is None or call.get("dispatched") is None or sequence is None:
                    uncertain = True
                elif (sequence <= boundary) == (phase == "before"):
                    matched = True
                else:
                    issues.append(_issue("steering", "工具参数与指令到达时点不符", rule["tool_name"], call))
            if uncertain:
                issues.append(_issue("missing_observation", "无法核实干预前后调用的顺序或参数", rule["tool_name"]))
            elif not matched:
                issues.append(_issue("steering", "对应阶段没有按干预契约执行工具", rule["tool_name"]))
        return issues

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定干预要求是否落实到研报与工具轨迹。

        Args:
            result: 运行结果对象。

        Returns:
            动态干预判定结果。
        """
        issues = []
        if not self.steering_keyword or not result.steering_applied:
            issues.append(_issue("missing_observation", "缺少干预关键词或指令注入记录"))
        else:
            if self.steering_keyword not in (result.report or ""):
                issues.append(_issue("steering", "报告未覆盖标注的干预要求"))
            if self.phases:
                issues.extend(self._boundary_issues(result))
        return _verdict("SteeringJudge", "trajectory", issues, "报告与干预契约相符")


def _fact_matches(value: str, paragraph: str) -> bool:
    """避免事实中的金额与型号成为另一数字或标识符的子串。"""
    prefix = r"(?<![A-Za-z0-9.])" if value[0].isascii() and value[0].isalnum() else ""
    if value[0].isdigit():
        prefix += r"(?<![-−－])(?<!\d[,，])"
    suffix = r"(?![A-Za-z0-9]|\.\d)" if value[-1].isascii() and value[-1].isalnum() else ""
    return bool(re.search(prefix + re.escape(value) + suffix, paragraph))


class ReferenceFactJudge:
    """逐条核对人工标注的事实表述及其实际可用的同段引用。"""

    def __init__(self, reference_facts: list[dict], forbidden_claims: list[str]) -> None:
        """初始化参考事实判定器。

        Args:
            reference_facts: 参考事实列表。
            forbidden_claims: 禁止结论表达式列表。
        """
        self.facts = reference_facts
        self.forbidden = forbidden_claims

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定研报事实与引用是否满足契约。

        Args:
            result: 运行结果对象。

        Returns:
            参考事实判定结果。
        """
        report = result.report or ""
        paragraphs = []
        for paragraph in re.split(r"\n\s*(?:\n|(?=[*+-] |\d+[.)] |\|))", report):
            citations = set(re.findall(r"\[([^\]\n]+)\]", paragraph))
            text = re.sub(r"\[[^\]\n]+\]", "", paragraph)
            paragraphs.append((text, citations))
        issues = []
        hits = unknown = 0
        for fact in self.facts:
            fact_id = fact["id"]
            refs = fact.get("evidence_ids", [])
            sources = [citations for text, citations in paragraphs
                       if any(_fact_matches(value, text) for value in fact["any_of"])]
            if not sources:
                issues.append(_issue("reference_fact", f"未命中参考事实 {fact_id}"))
            elif refs and not any(set(refs).issubset(citations) for citations in sources):
                issues.append(_issue("evidence_citation", f"事实 {fact_id} 未引用同段可用证据"))
            elif refs and result.evidence_ids is None:
                unknown += 1
                issues.append(_issue("missing_observation", f"未采集事实 {fact_id} 的可用证据清单"))
            elif refs and not set(refs).issubset(result.evidence_ids):
                issues.append(_issue("evidence_citation", f"事实 {fact_id} 引用了本次执行未取得的证据"))
            else:
                hits += 1
        for index, pattern in enumerate(self.forbidden):
            if re.search(pattern, report):
                issues.append(_issue("forbidden_claim", f"命中第 {index + 1} 条禁止结论"))
        return _verdict("ReferenceFactJudge", "outcome", issues, "标注事实和引用符合本例契约",
                        fact_hits=hits, fact_total=len(self.facts), fact_unknown=unknown)


class QuickAnswerJudge:
    """校验 Supervisor 是否按预期选择速答路径，且速答不产出完整研报。"""

    def __init__(self, expected: bool, max_chars: int = 800) -> None:
        """初始化速答路由判定器。

        Args:
            expected: 是否期望走速答路由。
            max_chars: 最大字符数。
        """
        self.expected = bool(expected)
        self.max_chars = max_chars

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定速答路由与正文长度是否符合预期。

        Args:
            result: 运行结果对象。

        Returns:
            速答路由判定结果。
        """
        quick = "research.quick_answer" in result.subagent_roles
        issues = []
        if quick != self.expected:
            issues.append(_issue("route", f"速答路由期望={self.expected}，实际={quick}"))
        if self.expected and len(result.report) > self.max_chars:
            issues.append(_issue("deliverable", f"速答正文超过 {self.max_chars} 字"))
        return _verdict("QuickAnswerJudge", "trajectory", issues, "速答路由与交付长度符合预期")


class CoordinatorToolJudge:
    """校验协调员直接工具调用是否留下可审计证据。"""

    def __init__(self, expected: bool) -> None:
        """初始化协调员工具判定器。

        Args:
            expected: 是否期望调用协调员工具。
        """
        self.expected = bool(expected)

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        """判定协调员工具是否按预期调用。

        Args:
            result: 运行结果对象。

        Returns:
            协调员工具判定结果。
        """
        used = "research.orchestrator" in result.evidence_roles
        issues = [] if used == self.expected else [
            _issue("route", f"协调员工具期望={self.expected}，实际={used}")
        ]
        return _verdict("CoordinatorToolJudge", "trajectory", issues, "协调员工具调用符合预期")


def evaluate_case(case: dict, result: AgentRunResult) -> list[JudgeVerdict]:
    """按用例声明调度判定器。运行异常优先归因，避免把观测缺失归咎于工具。"""
    boundary_judge = None
    if case.get("forbidden_tools") or "allowed_tools" in case:
        boundary_judge = ToolBoundaryJudge(case.get("allowed_tools"), case.get("forbidden_tools"))
    if result.system_status != "COMPLETED" or result.error:
        known_failure = bool(result.error) or result.system_status in {"FAILED", "CANCELLED", "TIMEOUT"}
        issue = _issue("run_status" if known_failure else "missing_observation", "任务异常结束或缺少可判定的终态")
        verdicts = [_verdict("ExecutionStatusJudge", "outcome", [issue], "")]
        if result.tool_calls and boundary_judge:
            boundary = boundary_judge.evaluate(result)
            if boundary.details["issues"]:
                verdicts.append(boundary)
        if result.report and case.get("forbidden_claims"):
            claims = ReferenceFactJudge([], case["forbidden_claims"]).evaluate(result)
            if claims.hard_failure:
                verdicts.append(claims)
        return verdicts
    verdicts = [DeliverableJudge(case.get("required_sections"), case.get("min_chars", 80)).evaluate(result),
                AntiHallucinationJudge().evaluate(result)]
    if case.get("required_tools") or case.get("tool_expectations") or case.get("tool_order") or "min_data_count" in case:
        verdicts.insert(0, ToolExecutionJudge(
            case.get("required_tools"), min_data_count=case.get("min_data_count", 1), tool_expectations=case.get("tool_expectations"),
            tool_order=case.get("tool_order"),
        ).evaluate(result))
    if boundary_judge:
        verdicts.append(boundary_judge.evaluate(result))
    if case.get("reference_facts") or case.get("forbidden_claims"):
        verdicts.append(ReferenceFactJudge(case.get("reference_facts", []), case.get("forbidden_claims", [])).evaluate(result))
    if case.get("steering_keyword"):
        phases = {phase: case["tool_expectations"][index] for phase, index in case.get("steering_boundary", {}).items()}
        verdicts.append(SteeringJudge(case["steering_keyword"], phases).evaluate(result))
    if case.get("max_tool_calls") is not None or case.get("max_elapsed_seconds") is not None:
        verdicts.append(BudgetJudge(case.get("max_tool_calls"), case.get("max_elapsed_seconds")).evaluate(result))
    if case.get("expected_quick_answer") is not None:
        verdicts.append(QuickAnswerJudge(bool(case["expected_quick_answer"])).evaluate(result))
    if case.get("expected_coordinator_tool") is not None:
        verdicts.append(CoordinatorToolJudge(bool(case["expected_coordinator_tool"])).evaluate(result))
    return verdicts
