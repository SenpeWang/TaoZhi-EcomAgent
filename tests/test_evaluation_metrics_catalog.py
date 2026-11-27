"""评测指标目录：按评测类型与口径定义唯一指标集合，并支持从批次指标提取标准摘要。"""
from __future__ import annotations

from taozhi.domain import evaluation_metrics as metrics

AGENT_FULL_KEYS = [
    "task_completion", "mean_score", "required_tool_coverage", "parameter_accuracy",
    "reference_fact_coverage", "call_coverage_among_known", "latency_p50_ms",
    "latency_p95_ms", "total_tokens", "failed_cases", "unassessable_cases",
]

RAG_FULL_KEYS = [
    "retrieval.hit@8", "retrieval.recall@8", "retrieval.mrr", "retrieval.precision@8",
    "retrieval.ndcg@8", "adversarial.pass_rate", "adversarial.canary_leaks",
    "l3_compliance_rate", "behavior.pass_rate", "behavior.faithfulness.micro",
    "six_metrics.answer_accuracy.accuracy", "behavior.false_refusal_rate",
    "behavior.citation_in_bounds", "behavior.fact_recall", "latency_e2e.p50",
    "latency_e2e.p95", "token_cost.total_tokens", "judge_token_cost.total_tokens",
]


def _labels(schema: dict) -> dict[str, str]:
    """把指标目录投影为指标键到中文标签的映射。

    Args:
        schema: 指标目录结构。
    """
    return {metric["key"]: metric["label"] for metric in schema["metrics"]}


def test_catalog_covers_every_kind_and_scope() -> None:
    """目录覆盖 Agent 与 RAG 的全部已定义口径，且每组指标都有完整展示元数据。"""
    catalog = metrics.catalog()
    assert catalog["version"] >= 2
    assert set(catalog["kinds"]) == {"agent", "rag"}
    assert set(catalog["kinds"]["agent"]["scopes"]) == {"full"}
    assert set(catalog["kinds"]["rag"]["scopes"]) == {
        "full", "quick", "adversarial", "retrieval", "behavior",
    }
    for kind in catalog["kinds"].values():
        for schema in kind["scopes"].values():
            assert schema["metrics"], schema["label"]
            for metric in schema["metrics"]:
                assert metric["key"] and metric["label"]
                assert metric["group"]
                assert metric["unit"] in {
                    "ratio", "percent", "seconds", "milliseconds", "tokens", "count", "score",
                }
                assert metric["direction"] in {"higher", "lower", "neutral"}


def test_metric_labels_use_common_terminology() -> None:
    """展示名使用通用评测术语（中英对照），不使用自造简称。"""
    banned = ("必调", "声明级忠实度", "行为通过率", "事实与引用覆盖", "调用覆盖率")
    labels = {}
    for kind in metrics.catalog()["kinds"].values():
        for schema in kind["scopes"].values():
            labels.update(_labels(schema))
    for label in labels.values():
        assert "（" in label and "）" in label, label
        assert not any(term in label for term in banned), label
    agent = _labels(metrics.schema("agent", "full"))
    assert "Task Success Rate" in agent["task_completion"]
    assert "Required Tool Coverage" in agent["required_tool_coverage"]
    assert "Fact Recall" in agent["reference_fact_coverage"]
    rag = _labels(metrics.schema("rag", "full"))
    assert "Hit@8" in rag["retrieval.hit@8"]
    assert "Faithfulness" in rag["behavior.faithfulness.micro"]
    assert "False Refusal Rate" in rag["behavior.false_refusal_rate"]
    assert "L3 Compliance" in rag["l3_compliance_rate"]


def test_same_scope_schema_is_stable() -> None:
    """同一（类型+口径）重复读取必须得到完全一致的指标集合。"""
    first = metrics.schema("rag", "full")
    second = metrics.schema("rag", "full")
    assert first == second
    assert [metric["key"] for metric in first["metrics"]] == RAG_FULL_KEYS


def test_schema_undefined_scope_returns_none() -> None:
    """未定义的口径或类型不返回 schema，避免界面自行拼装指标。"""
    assert metrics.schema("rag", "unknown") is None
    assert metrics.schema("agent", "quick") is None
    assert metrics.schema(None, "full") is None


def test_extract_agent_metrics_from_run_and_nested_paths() -> None:
    """Agent 摘要同时取批次主字段与报告指标路径，缺失项返回 None。"""
    summary = metrics.extract(
        "agent", "full",
        {"required_tool_coverage": {"rate": 0.75}, "parameter_accuracy": {"rate": 0.5}},
        {"pass_rate": 0.9, "mean_score": 0.8, "latency_p50_ms": 120, "total_tokens": 500,
         "failed_cases": 1, "unassessable_cases": 2},
    )
    assert list(summary) == AGENT_FULL_KEYS
    assert summary["task_completion"] == 0.9
    assert summary["mean_score"] == 0.8
    assert summary["required_tool_coverage"] == 0.75
    assert summary["parameter_accuracy"] == 0.5
    assert summary["reference_fact_coverage"] is None
    assert summary["latency_p50_ms"] == 120
    assert summary["total_tokens"] == 500
    assert summary["failed_cases"] == 1
    assert summary["unassessable_cases"] == 2


def test_extract_rag_metrics_from_layered_report() -> None:
    """RAG 摘要按层从原始报告指标提取，覆盖安全、回答质量与成本分账。"""
    report = {
        "retrieval": {"hit@8": 0.9, "recall@8": 0.8, "mrr": 0.7,
                      "precision@8": 0.5, "ndcg@8": 0.6},
        "adversarial": {"pass_rate": 0.5, "canary_leaks": 0},
        "behavior": {"pass_rate": 0.6, "faithfulness": {"micro": 0.85},
                     "refusal": {"false_refusal_rate": 0.1},
                     "citation": {"in_bounds_rate": 1.0},
                     "fact_recall": {"mean": 0.55}},
        "six_metrics": {
            "answer_accuracy": {"accuracy": 0.55},
            "latency_e2e_s": {"p50": 12.5, "p95": 30.0},
            "token_cost": {"total_tokens": 1234},
            "judge_token_cost": {"total_tokens": 567},
            "quality_extended": {"l3_compliance": {"compliance_rate": 1.0}},
        },
    }
    summary = metrics.extract("rag", "full", report, {})
    assert list(summary) == RAG_FULL_KEYS
    assert summary["retrieval.hit@8"] == 0.9
    assert summary["retrieval.precision@8"] == 0.5
    assert summary["retrieval.ndcg@8"] == 0.6
    assert summary["adversarial.canary_leaks"] == 0.0
    assert summary["l3_compliance_rate"] == 1.0
    assert summary["behavior.faithfulness.micro"] == 0.85
    assert summary["behavior.false_refusal_rate"] == 0.1
    assert summary["behavior.citation_in_bounds"] == 1.0
    assert summary["behavior.fact_recall"] == 0.55
    assert summary["latency_e2e.p95"] == 30.0
    assert summary["token_cost.total_tokens"] == 1234.0
    assert summary["judge_token_cost.total_tokens"] == 567.0


def test_extract_rejects_bool_and_missing_metrics() -> None:
    """布尔值不算指标数值，空报告返回全 None 摘要。"""
    summary = metrics.extract("agent", "full", {}, {"pass_rate": True, "mean_score": None})
    assert summary["task_completion"] is None
    assert summary["mean_score"] is None
    assert metrics.extract("rag", "full", None, None)["retrieval.hit@8"] is None
