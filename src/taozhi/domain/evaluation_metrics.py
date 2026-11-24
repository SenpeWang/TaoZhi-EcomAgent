"""评测指标口径目录：按评测类型与口径定义唯一、可复用的指标集合。

同一（评测类型 + 口径）的批次必须使用同一组指标键与单位，界面与接口都从本目录渲染；
不同类型/口径的指标不同属于预期。指标值从批次的 `metrics` 原始报告或批次主字段提取，
不在展示层自行拼装或改名。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MetricSpec:
    """单个指标的口径定义。

    Attributes:
        key: 指标键，批次摘要与界面列的唯一标识。
        label: 中文展示名。
        group: 指标分组（检索/对抗/行为/工程/执行）。
        unit: 数值单位：ratio / percent / seconds / milliseconds / tokens / count。
        source: 取值来源：run 表示批次主字段，metrics 表示原始报告指标。
        path: run 模式为字段名，metrics 模式为指标路径。
        direction: 越高越好 higher、越低越好 lower、中性 neutral。
        description: 口径说明，界面悬浮提示使用。
    """

    key: str
    label: str
    group: str
    unit: str
    source: str
    path: tuple[str, ...]
    direction: str = "higher"
    description: str = ""


def _run(key: str, label: str, group: str, unit: str, field: str, *,
         direction: str = "higher", description: str = "") -> MetricSpec:
    return MetricSpec(key, label, group, unit, "run", (field,), direction, description)


def _metric(key: str, label: str, group: str, unit: str, *path: str,
            direction: str = "higher", description: str = "") -> MetricSpec:
    return MetricSpec(key, label, group, unit, "metrics", path, direction, description)


AGENT_FULL: tuple[MetricSpec, ...] = (
    _run("task_completion", "任务完成率", "执行", "ratio", "pass_rate",
         description="通过用例数 ÷ 批次用例总数，未判定计入分母"),
    _run("mean_score", "平均得分", "执行", "score", "mean_score"),
    _metric("required_tool_coverage", "必调工具覆盖率", "工具执行", "ratio",
            "required_tool_coverage", "rate"),
    _metric("parameter_accuracy", "参数准确率", "工具执行", "ratio",
            "parameter_accuracy", "rate"),
    _metric("reference_fact_coverage", "事实与引用覆盖", "事实", "ratio",
            "reference_fact_coverage", "rate"),
    _metric("call_coverage_among_known", "调用覆盖率", "工具执行", "ratio",
            "call_coverage_among_known", "rate"),
    _run("latency_p50_ms", "耗时 P50", "性能", "milliseconds", "latency_p50_ms",
         direction="lower"),
    _run("latency_p95_ms", "耗时 P95", "性能", "milliseconds", "latency_p95_ms",
         direction="lower"),
    _run("total_tokens", "Token 总量", "成本", "tokens", "total_tokens",
         direction="lower"),
)

RAG_FULL: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "检索 Hit@8", "检索", "ratio", "retrieval", "hit@8"),
    _metric("retrieval.recall@8", "检索 Recall@8", "检索", "ratio", "retrieval", "recall@8"),
    _metric("retrieval.mrr", "检索 MRR", "检索", "ratio", "retrieval", "mrr"),
    _metric("adversarial.pass_rate", "对抗通过率", "对抗", "ratio", "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏", "对抗", "count", "adversarial",
            "canary_leaks", direction="lower"),
    _metric("behavior.pass_rate", "行为通过率", "行为", "ratio", "behavior", "pass_rate"),
    _metric("behavior.faithfulness.micro", "声明级忠实度", "行为", "ratio",
            "behavior", "faithfulness", "micro"),
    _metric("six_metrics.answer_accuracy.accuracy", "答案准确率", "行为", "ratio",
            "six_metrics", "answer_accuracy", "accuracy"),
    _metric("latency_e2e.p50", "端到端 P50", "性能", "seconds", "six_metrics",
            "latency_e2e_s", "p50", direction="lower"),
    _metric("latency_e2e.p95", "端到端 P95", "性能", "seconds", "six_metrics",
            "latency_e2e_s", "p95", direction="lower"),
    _metric("token_cost.total_tokens", "Token 总量", "成本", "tokens",
            "six_metrics", "token_cost", "total_tokens", direction="lower"),
)

RAG_ADVERSARIAL: tuple[MetricSpec, ...] = (
    _metric("adversarial.pass_rate", "对抗通过率", "对抗", "ratio", "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏", "对抗", "count", "adversarial",
            "canary_leaks", direction="lower"),
    _metric("adversarial.rejected", "被拒绝数", "对抗", "count", "adversarial", "rejected",
            direction="neutral"),
    _metric("adversarial.latency_p95_s", "对抗 P95", "性能", "seconds",
            "adversarial", "slo", "p95_s", direction="lower"),
    _metric("adversarial.tokens", "对抗 Token", "成本", "tokens",
            "adversarial", "sut_cost", "total_tokens", direction="lower"),
)

RAG_QUICK: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "检索 Hit@8", "检索", "ratio", "retrieval", "hit@8"),
    _metric("retrieval.recall@8", "检索 Recall@8", "检索", "ratio", "retrieval", "recall@8"),
    _metric("retrieval.mrr", "检索 MRR", "检索", "ratio", "retrieval", "mrr"),
    _metric("adversarial.pass_rate", "对抗通过率", "对抗", "ratio", "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏", "对抗", "count", "adversarial",
            "canary_leaks", direction="lower"),
    _metric("latency_e2e.p95", "端到端 P95", "性能", "seconds", "six_metrics",
            "latency_e2e_s", "p95", direction="lower"),
    _metric("token_cost.total_tokens", "Token 总量", "成本", "tokens",
            "six_metrics", "token_cost", "total_tokens", direction="lower"),
)

RAG_RETRIEVAL: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "检索 Hit@8", "检索", "ratio", "retrieval", "hit@8"),
    _metric("retrieval.recall@8", "检索 Recall@8", "检索", "ratio", "retrieval", "recall@8"),
    _metric("retrieval.mrr", "检索 MRR", "检索", "ratio", "retrieval", "mrr"),
    _metric("retrieval.ndcg@8", "检索 nDCG@8", "检索", "ratio", "retrieval", "ndcg@8"),
    _metric("retrieval.latency_p95_s", "检索 P95", "性能", "seconds",
            "retrieval", "latency_p95_s", direction="lower"),
    _metric("retrieval.tokens", "检索 Token", "成本", "tokens",
            "retrieval", "sut_cost", "total_tokens", direction="lower"),
)

RAG_BEHAVIOR: tuple[MetricSpec, ...] = (
    _metric("behavior.pass_rate", "行为通过率", "行为", "ratio", "behavior", "pass_rate"),
    _metric("behavior.faithfulness.micro", "声明级忠实度", "行为", "ratio",
            "behavior", "faithfulness", "micro"),
    _metric("behavior.answer_accuracy", "答案准确率", "行为", "ratio",
            "behavior", "answer_accuracy", "accuracy"),
    _metric("behavior.fact_recall", "事实召回", "行为", "ratio", "behavior", "fact_recall", "mean"),
    _metric("behavior.false_refusal_rate", "误拒率", "行为", "ratio",
            "behavior", "refusal", "false_refusal_rate", direction="lower"),
    _metric("behavior.citation_in_bounds", "引用合规率", "行为", "ratio",
            "behavior", "citation", "in_bounds_rate"),
    _metric("behavior.latency_p95_s", "行为 P95", "性能", "seconds",
            "behavior", "slo", "p95_s", direction="lower"),
    _metric("behavior.tokens", "行为 Token", "成本", "tokens",
            "behavior", "sut_cost", "total_tokens", direction="lower"),
)

SCOPES: dict[str, dict[str, tuple[MetricSpec, ...]]] = {
    "agent": {"full": AGENT_FULL},
    "rag": {
        "full": RAG_FULL,
        "quick": RAG_QUICK,
        "adversarial": RAG_ADVERSARIAL,
        "retrieval": RAG_RETRIEVAL,
        "behavior": RAG_BEHAVIOR,
    },
}

KIND_LABELS = {"agent": "Agent 调研评测", "rag": "RAG 知识问答评测"}
SCOPE_LABELS = {
    "full": "全量",
    "quick": "快速",
    "adversarial": "对抗层",
    "retrieval": "检索层",
    "behavior": "行为层",
}


def _specs(eval_kind: str, scope: str) -> tuple[MetricSpec, ...]:
    return SCOPES.get(eval_kind, {}).get(scope, ())


def _serialise(specs: Iterable[MetricSpec]) -> list[dict[str, Any]]:
    return [
        {"key": spec.key, "label": spec.label, "group": spec.group, "unit": spec.unit,
         "direction": spec.direction, "description": spec.description}
        for spec in specs
    ]


def schema(eval_kind: str | None, scope: str | None) -> dict[str, Any] | None:
    """返回（评测类型，口径）对应的指标 schema；未定义时返回 None。"""
    if not eval_kind or not scope:
        return None
    specs = _specs(eval_kind, scope)
    if not specs:
        return None
    return {
        "version": SCHEMA_VERSION,
        "eval_kind": eval_kind,
        "scope": scope,
        "label": f"{KIND_LABELS.get(eval_kind, eval_kind)} · {SCOPE_LABELS.get(scope, scope)}",
        "metrics": _serialise(specs),
    }


def catalog() -> dict[str, Any]:
    """返回完整指标目录：按评测类型与口径列出全部指标定义。"""
    kinds = {}
    for eval_kind, scopes in SCOPES.items():
        kinds[eval_kind] = {
            "label": KIND_LABELS.get(eval_kind, eval_kind),
            "scopes": {
                scope: {
                    "version": SCHEMA_VERSION,
                    "eval_kind": eval_kind,
                    "scope": scope,
                    "label": f"{KIND_LABELS.get(eval_kind, eval_kind)} · "
                             f"{SCOPE_LABELS.get(scope, scope)}",
                    "metrics": _serialise(specs),
                }
                for scope, specs in scopes.items()
            },
        }
    return {"version": SCHEMA_VERSION, "kinds": kinds}


def _dig(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def extract(eval_kind: str | None, scope: str | None, metrics: dict | None,
            run_fields: dict | None) -> dict[str, float | None]:
    """从批次指标与主字段中提取该口径的标准摘要，缺失项返回 None。"""
    summary: dict[str, float | None] = {}
    for spec in _specs(eval_kind or "", scope or ""):
        if spec.source == "run":
            value = (run_fields or {}).get(spec.path[0])
        else:
            value = _dig(metrics or {}, spec.path)
        summary[spec.key] = _number(value)
    return summary