"""评测指标口径目录：按评测类型与口径定义唯一、可复用的指标集合。

同一（评测类型 + 口径）的批次必须使用同一组指标键与单位，界面与接口都从本目录渲染；
不同类型/口径的指标不同属于预期。指标值从批次的 `metrics` 原始报告或批次主字段提取，
不在展示层自行拼装或改名。展示名优先采用检索（IR）、RAGAS、Agent 评测与安全评测的
通用术语，中英对照，避免自造简称。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class MetricSpec:
    """单个指标的口径定义。

    Attributes:
        key: 指标键，批次摘要与界面列的唯一标识。
        label: 中英对照展示名，采用通用评测术语。
        group: 指标分组（任务效果/工具调用/事实性/检索/安全/回答质量/性能/成本）。
        unit: 数值单位：ratio / percent / seconds / milliseconds / tokens / count / score。
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
    """构造取值来自批次主字段的指标定义。

    Args:
        key: 指标键。
        label: 展示名。
        group: 指标分组。
        unit: 数值单位。
        field: 批次主字段名。
        direction: 优劣方向。
        description: 口径说明。

    Returns:
        指标定义。
    """
    return MetricSpec(key, label, group, unit, "run", (field,), direction, description)


def _metric(key: str, label: str, group: str, unit: str, *path: str,
            direction: str = "higher", description: str = "") -> MetricSpec:
    """构造取值来自原始报告指标的指标定义。

    Args:
        key: 指标键。
        label: 展示名。
        group: 指标分组。
        unit: 数值单位。
        path: 原始报告中的指标路径。
        direction: 优劣方向。
        description: 口径说明。

    Returns:
        指标定义。
    """
    return MetricSpec(key, label, group, unit, "metrics", path, direction, description)


AGENT_FULL: tuple[MetricSpec, ...] = (
    _run("task_completion", "任务成功率（Task Success Rate）", "任务效果", "ratio", "pass_rate",
         description="通过用例数 ÷ 可判定用例数；不可判定用例单列"),
    _run("mean_score", "平均分（Average Score）", "任务效果", "score", "mean_score"),
    _metric("required_tool_coverage", "必需工具覆盖率（Required Tool Coverage）", "工具调用",
            "ratio", "required_tool_coverage", "rate",
            description="命中必需工具数 ÷ 必需工具总数，不把未知工具计入分母"),
    _metric("parameter_accuracy", "参数准确率（Argument Accuracy）", "工具调用", "ratio",
            "parameter_accuracy", "rate"),
    _metric("reference_fact_coverage", "事实召回率（Fact Recall）", "事实性", "ratio",
            "reference_fact_coverage", "rate",
            description="命中的参考事实数 ÷ 参考事实总数"),
    _metric("call_coverage_among_known", "模型调用计量覆盖率（Call Coverage）", "工程", "ratio",
            "call_coverage_among_known", "rate",
            description="已计量模型调用数 ÷ 已知模型调用数，不代表工具调用质量"),
    _run("latency_p50_ms", "P50 延迟（Latency）", "性能", "milliseconds", "latency_p50_ms",
         direction="lower"),
    _run("latency_p95_ms", "P95 延迟（Latency）", "性能", "milliseconds", "latency_p95_ms",
         direction="lower"),
    _run("total_tokens", "Token 用量（Token Usage）", "成本", "tokens", "total_tokens",
         direction="lower"),
    _run("failed_cases", "失败用例数（Failed Cases）", "任务效果", "count", "failed_cases",
         direction="lower"),
    _run("unassessable_cases", "不可判定用例数（Unassessable Cases）", "任务效果", "count",
         "unassessable_cases", direction="lower"),
)

RAG_FULL: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "Hit@8（命中率）", "检索", "ratio", "retrieval", "hit@8",
            description="命中至少一个预期文档的用例占比"),
    _metric("retrieval.recall@8", "Recall@8（召回率）", "检索", "ratio", "retrieval", "recall@8",
            description="召回的相关文档数 ÷ 全部相关文档数"),
    _metric("retrieval.mrr", "MRR（平均倒数排名）", "检索", "ratio", "retrieval", "mrr"),
    _metric("retrieval.precision@8", "Precision@8（精确率）", "检索", "ratio",
            "retrieval", "precision@8",
            description="前 8 条中相关文档占比，分母固定为 8"),
    _metric("retrieval.ndcg@8", "nDCG@8（归一化折损累计增益）", "检索", "ratio",
            "retrieval", "ndcg@8"),
    _metric("adversarial.pass_rate", "对抗用例通过率（Adversarial Pass Rate）", "安全", "ratio",
            "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏数（Canary Leaks）", "安全", "count",
            "adversarial", "canary_leaks", direction="lower"),
    _metric("l3_compliance_rate", "L3 合规率（L3 Compliance）", "安全", "ratio",
            "six_metrics", "quality_extended", "l3_compliance", "compliance_rate",
            description="涉密拦截场景中未发生外部模型调用的比例，未测时为未判定"),
    _metric("behavior.pass_rate", "回答通过率（Pass Rate）", "回答质量", "ratio",
            "behavior", "pass_rate"),
    _metric("behavior.faithfulness.micro", "忠实度（Faithfulness）", "回答质量", "ratio",
            "behavior", "faithfulness", "micro",
            description="声明级（claim-level）判定：被答案支持的声明数 ÷ 声明总数"),
    _metric("six_metrics.answer_accuracy.accuracy", "答案准确率（Answer Accuracy）", "回答质量",
            "ratio", "six_metrics", "answer_accuracy", "accuracy"),
    _metric("behavior.false_refusal_rate", "误拒率（False Refusal Rate）", "回答质量", "ratio",
            "behavior", "refusal", "false_refusal_rate", direction="lower",
            description="本可回答却被拒答的比例，越低越好"),
    _metric("behavior.citation_in_bounds", "引用准确率（Citation Accuracy）", "回答质量", "ratio",
            "behavior", "citation", "in_bounds_rate",
            description="引用全部命中可访问且属于答案证据范围的比例"),
    _metric("behavior.fact_recall", "事实召回率（Fact Recall）", "回答质量", "ratio",
            "behavior", "fact_recall", "mean"),
    _metric("latency_e2e.p50", "P50 端到端延迟（E2E Latency）", "性能", "seconds",
            "six_metrics", "latency_e2e_s", "p50", direction="lower"),
    _metric("latency_e2e.p95", "P95 端到端延迟（E2E Latency）", "性能", "seconds",
            "six_metrics", "latency_e2e_s", "p95", direction="lower"),
    _metric("token_cost.total_tokens", "被测系统 Token 用量（SUT Token Usage）", "成本", "tokens",
            "six_metrics", "token_cost", "total_tokens", direction="lower"),
    _metric("judge_token_cost.total_tokens", "评审模型 Token 用量（Judge Token Usage）", "成本",
            "tokens", "six_metrics", "judge_token_cost", "total_tokens", direction="lower"),
)

RAG_ADVERSARIAL: tuple[MetricSpec, ...] = (
    _metric("adversarial.pass_rate", "对抗用例通过率（Adversarial Pass Rate）", "安全", "ratio",
            "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏数（Canary Leaks）", "安全", "count",
            "adversarial", "canary_leaks", direction="lower"),
    _metric("adversarial.rejected", "拒绝回答数（Rejected）", "安全", "count",
            "adversarial", "rejected", direction="neutral"),
    _metric("l3_compliance_rate", "L3 合规率（L3 Compliance）", "安全", "ratio",
            "six_metrics", "quality_extended", "l3_compliance", "compliance_rate"),
    _metric("adversarial.latency_p95_s", "P95 延迟（Latency）", "性能", "seconds",
            "adversarial", "slo", "p95_s", direction="lower"),
    _metric("adversarial.tokens", "被测系统 Token 用量（SUT Token Usage）", "成本", "tokens",
            "adversarial", "sut_cost", "total_tokens", direction="lower"),
)

RAG_QUICK: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "Hit@8（命中率）", "检索", "ratio", "retrieval", "hit@8"),
    _metric("retrieval.recall@8", "Recall@8（召回率）", "检索", "ratio", "retrieval", "recall@8"),
    _metric("retrieval.mrr", "MRR（平均倒数排名）", "检索", "ratio", "retrieval", "mrr"),
    _metric("adversarial.pass_rate", "对抗用例通过率（Adversarial Pass Rate）", "安全", "ratio",
            "adversarial", "pass_rate"),
    _metric("adversarial.canary_leaks", "金丝雀泄漏数（Canary Leaks）", "安全", "count",
            "adversarial", "canary_leaks", direction="lower"),
    _metric("latency_e2e.p95", "P95 端到端延迟（E2E Latency）", "性能", "seconds",
            "six_metrics", "latency_e2e_s", "p95", direction="lower"),
    _metric("token_cost.total_tokens", "被测系统 Token 用量（SUT Token Usage）", "成本", "tokens",
            "six_metrics", "token_cost", "total_tokens", direction="lower"),
)

RAG_RETRIEVAL: tuple[MetricSpec, ...] = (
    _metric("retrieval.hit@8", "Hit@8（命中率）", "检索", "ratio", "retrieval", "hit@8"),
    _metric("retrieval.recall@8", "Recall@8（召回率）", "检索", "ratio", "retrieval", "recall@8"),
    _metric("retrieval.mrr", "MRR（平均倒数排名）", "检索", "ratio", "retrieval", "mrr"),
    _metric("retrieval.ndcg@8", "nDCG@8（归一化折损累计增益）", "检索", "ratio",
            "retrieval", "ndcg@8"),
    _metric("retrieval.latency_p95_s", "P95 检索延迟（Retrieval Latency）", "性能", "seconds",
            "retrieval", "latency_p95_s", direction="lower"),
    _metric("retrieval.tokens", "被测系统 Token 用量（SUT Token Usage）", "成本", "tokens",
            "retrieval", "sut_cost", "total_tokens", direction="lower"),
)

RAG_BEHAVIOR: tuple[MetricSpec, ...] = (
    _metric("behavior.pass_rate", "回答通过率（Pass Rate）", "回答质量", "ratio",
            "behavior", "pass_rate"),
    _metric("behavior.faithfulness.micro", "忠实度（Faithfulness）", "回答质量", "ratio",
            "behavior", "faithfulness", "micro",
            description="声明级（claim-level）判定：被答案支持的声明数 ÷ 声明总数"),
    _metric("behavior.answer_accuracy", "答案准确率（Answer Accuracy）", "回答质量", "ratio",
            "behavior", "answer_accuracy", "accuracy"),
    _metric("behavior.fact_recall", "事实召回率（Fact Recall）", "回答质量", "ratio",
            "behavior", "fact_recall", "mean"),
    _metric("behavior.false_refusal_rate", "误拒率（False Refusal Rate）", "回答质量", "ratio",
            "behavior", "refusal", "false_refusal_rate", direction="lower",
            description="本可回答却被拒答的比例，越低越好"),
    _metric("behavior.citation_in_bounds", "引用准确率（Citation Accuracy）", "回答质量", "ratio",
            "behavior", "citation", "in_bounds_rate",
            description="引用全部命中可访问且属于答案证据范围的比例"),
    _metric("behavior.latency_p95_s", "P95 延迟（Latency）", "性能", "seconds",
            "behavior", "slo", "p95_s", direction="lower"),
    _metric("behavior.tokens", "被测系统 Token 用量（SUT Token Usage）", "成本", "tokens",
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
    "adversarial": "对抗测试",
    "retrieval": "检索测试",
    "behavior": "回答质量",
}


def _specs(eval_kind: str, scope: str) -> tuple[MetricSpec, ...]:
    """返回（评测类型，口径）对应的指标定义元组；未定义时为空元组。

    Args:
        eval_kind: 评测类型。
        scope: 评测口径。

    Returns:
        该组合的指标定义元组。
    """
    return SCOPES.get(eval_kind, {}).get(scope, ())


def _serialise(specs: Iterable[MetricSpec]) -> list[dict[str, Any]]:
    """把指标定义序列化为接口返回的字典列表。

    Args:
        specs: 指标定义集合。

    Returns:
        含 key、label、group、unit、direction、description 的字典列表。
    """
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
    """按路径逐层读取嵌套字典中的指标值。

    Args:
        value: 待读取的嵌套字典。
        path: 字段路径。

    Returns:
        路径命中的值；任一层缺失时返回 None。
    """
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _number(value: Any) -> float | None:
    """把指标值归一为 float；布尔与非数值返回 None。

    Args:
        value: 待归一的值。

    Returns:
        浮点数值或 None。
    """
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
