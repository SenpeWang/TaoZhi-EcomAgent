"""在执行前检查 Agent 评测集及其可判定的期望。"""
from __future__ import annotations

import re
from graphlib import CycleError, TopologicalSorter

from taozhi.domain.research import ALL_RESEARCH_TOOLS

CASE_FIELDS = {
    "id", "category", "name", "prompt", "keyword", "suite", "critical",
    "required_tools", "forbidden_tools", "allowed_tools", "tool_expectations", "tool_order",
    "reference_facts", "forbidden_claims", "required_sections", "min_chars", "min_data_count",
    "steering", "steering_keyword",
}


def _strings(values: object, field: str) -> set[str]:
    if not isinstance(values, list) or any(not isinstance(s, str) or not s.strip() for s in values):
        raise ValueError(f"{field} 需要非空字符串组成的数组")
    if len(set(values)) != len(values):
        raise ValueError(f"{field} 有重复值")
    return set(values)


def _validate_tools(case: dict) -> None:
    registered = set(ALL_RESEARCH_TOOLS)
    required = _strings(case.get("required_tools", []), "required_tools")
    allowed = _strings(case.get("allowed_tools", list(ALL_RESEARCH_TOOLS)), "allowed_tools")
    forbidden = _strings(case.get("forbidden_tools", []), "forbidden_tools")
    if (allowed | forbidden | required) - registered:
        raise ValueError("工具名称未在调研工具目录中注册")
    available = allowed - forbidden
    if required - available:
        raise ValueError("必调工具超出允许范围")

    expectations = case.get("tool_expectations", [])
    if not isinstance(expectations, list):
        raise ValueError("tool_expectations 需要数组")
    for expectation in expectations:
        if not isinstance(expectation, dict) or expectation.get("tool_name") not in available:
            raise ValueError("工具期望缺少允许的 tool_name")
        if expectation.keys() - {"tool_name", "parameters", "status", "min_data_count"}:
            raise ValueError("工具期望中存在未识别字段")
        if "status" in expectation and expectation["status"] not in {"ok", "empty", "error", "timeout", "cancelled"}:
            raise ValueError("无法识别预期工具状态")
        minimum = expectation.get("min_data_count", 0)
        if type(minimum) is not int or minimum < 0 or (minimum and expectation.get("status") == "empty"):
            raise ValueError("预期条数与工具状态不一致")
        parameters = expectation.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("parameters 需要参数名与实际匹配条件")
        for key, condition in parameters.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("参数名不能为空")
            if isinstance(condition, dict) and (
                condition.keys() != {"contains"} or not isinstance(condition["contains"], str) or not condition["contains"]
            ):
                raise ValueError("字符串包含匹配需要非空 contains，其余参数直接使用期望值")

    order = case.get("tool_order", [])
    if not isinstance(order, list):
        raise ValueError("tool_order 需要有向依赖对数组")
    graph = TopologicalSorter()
    for pair in order:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(tool not in available for tool in pair):
            raise ValueError("工具依赖需要两个获准的工具名")
        graph.add(pair[1], pair[0])
    try:
        graph.prepare()
    except CycleError as exc:
        raise ValueError("工具先后依赖不能成环") from exc


def _validate_reference(case: dict) -> None:
    ids = set()
    facts = case.get("reference_facts", [])
    if not isinstance(facts, list):
        raise ValueError("reference_facts 需要数组")
    for fact in facts:
        if not isinstance(fact, dict) or fact.keys() - {"id", "any_of", "evidence_ids"}:
            raise ValueError("参考事实包含无效字段")
        fact_id = fact.get("id")
        if not isinstance(fact_id, str) or not fact_id.strip() or fact_id in ids:
            raise ValueError("参考事实需要唯一 ID")
        ids.add(fact_id)
        if not _strings(fact.get("any_of"), "any_of"):
            raise ValueError("参考事实至少需要一种可接受表述")
        _strings(fact.get("evidence_ids", []), "evidence_ids")
    for pattern in _strings(case.get("forbidden_claims", []), "forbidden_claims"):
        try:
            forbidden = re.compile(pattern)
        except re.error as exc:
            raise ValueError("禁止结论的表达式无效") from exc
        if forbidden.search(""):
            raise ValueError("禁止结论的表达式不能匹配空文本")


def validate_cases(cases: list[dict]) -> None:
    """拒绝重复用例、矛盾的工具契约和无法判定的参考事实。"""
    if not cases:
        raise ValueError("至少需要一个评测用例")
    seen = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError("用例 ID 为空或重复")
        seen.add(case_id)
        try:
            if case.keys() - CASE_FIELDS:
                raise ValueError("存在未登记的字段或尚未支持的判定条件")
            if case.get("suite", "benchmark") not in {"benchmark", "golden", "regression", "challenge"}:
                raise ValueError("未知的用例分组")
            if not all(isinstance(case.get(key), str) and case[key].strip() for key in ("name", "prompt")):
                raise ValueError("用例需要名称和用户输入")
            if "critical" in case and type(case["critical"]) is not bool:
                raise ValueError("critical 需要布尔值")
            for key in ("min_chars", "min_data_count"):
                if key in case and (type(case[key]) is not int or case[key] < 0):
                    raise ValueError(f"{key} 需要非负整数")
            _strings(case.get("required_sections", []), "required_sections")
            steering = _strings(case.get("steering", []), "steering")
            marker = case.get("steering_keyword", "")
            if not isinstance(marker, str) or bool(steering) != bool(marker.strip()):
                raise ValueError("干预指令和预期采纳关键词需同时声明")
            _validate_tools(case)
            _validate_reference(case)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{case_id} 的评测契约无效：{exc}") from exc
