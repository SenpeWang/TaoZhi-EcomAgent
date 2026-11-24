"""评测指标目录：按评测类型与口径定义唯一指标集合，并支持从批次指标提取标准摘要。"""
from __future__ import annotations

from taozhi.domain import evaluation_metrics as metrics


def test_catalog_covers_every_kind_and_scope() -> None:
    """目录覆盖 Agent 与 RAG 的全部已定义口径，且每组指标都有完整展示元数据。"""
    catalog = metrics.catalog()
    assert catalog["version"] >= 1
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
                assert metric["unit"] in {
                    "ratio", "percent", "seconds", "milliseconds", "tokens", "count", "score",
                }
                assert metric["direction"] in {"higher", "lower", "neutral"}


def test_same_scope_schema_is_stable() -> None:
    """同一（类型+口径）重复读取必须得到完全一致的指标集合。"""
    first = metrics.schema("rag", "full")
    second = metrics.schema("rag", "full")
    assert first == second
    assert [metric["key"] for metric in first["metrics"]] == [
        metric["key"] for metric in metrics.catalog()["kinds"]["rag"]["scopes"]["full"]["metrics"]
    ]


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
        {"pass_rate": 0.9, "mean_score": 0.8, "latency_p50_ms": 120, "total_tokens": 500},
    )
    assert summary["task_completion"] == 0.9
    assert summary["mean_score"] == 0.8
    assert summary["required_tool_coverage"] == 0.75
    assert summary["parameter_accuracy"] == 0.5
    assert summary["reference_fact_coverage"] is None
    assert summary["latency_p50_ms"] == 120
    assert summary["total_tokens"] == 500


def test_extract_rag_metrics_from_layered_report() -> None:
    """RAG 摘要按层从原始报告指标提取，同一口径键顺序固定。"""
    report = {
        "retrieval": {"hit@8": 0.9, "recall@8": 0.8, "mrr": 0.7},
        "adversarial": {"pass_rate": 0.5, "canary_leaks": 0},
        "behavior": {"pass_rate": 0.6, "faithfulness": {"micro": 0.85}},
        "six_metrics": {"answer_accuracy": {"accuracy": 0.55},
                        "latency_e2e_s": {"p50": 12.5, "p95": 30.0},
                        "token_cost": {"total_tokens": 1234}},
    }
    summary = metrics.extract("rag", "full", report, {})
    assert list(summary) == [metric["key"] for metric in metrics.schema("rag", "full")["metrics"]]
    assert summary["retrieval.hit@8"] == 0.9
    assert summary["adversarial.canary_leaks"] == 0.0
    assert summary["behavior.faithfulness.micro"] == 0.85
    assert summary["latency_e2e.p95"] == 30.0
    assert summary["token_cost.total_tokens"] == 1234.0
    assert summary["six_metrics.answer_accuracy.accuracy"] == 0.55


def test_extract_rejects_bool_and_missing_metrics() -> None:
    """布尔值不算指标数值，空报告返回全 None 摘要。"""
    summary = metrics.extract("agent", "full", {}, {"pass_rate": True, "mean_score": None})
    assert summary["task_completion"] is None
    assert summary["mean_score"] is None
    assert metrics.extract("rag", "full", None, None)["retrieval.hit@8"] is None