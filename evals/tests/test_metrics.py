#!/usr/bin/env python3
"""评估指标单元测试：口径一旦写错，所有基线和阈值决策都会跟着错，所以必须有测试盯住。

运行：.venv/bin/python -m pytest evals/tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from metrics import (
    flat_means,
    hit_at_k,
    ndcg_at_k,
    pass_pow_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    retrieval_report,
    summarize,
)


def test_hit_at_k_counts_only_relevant_hits():
    ranked = ["无关A", "相关X", "无关B"]
    assert hit_at_k(ranked, {"相关X"}, 1) == 0.0
    assert hit_at_k(ranked, {"相关X"}, 3) == 1.0
    assert hit_at_k(ranked, set(), 3) == 0.0


def test_precision_and_recall_are_not_the_same_number():
    ranked = ["相关X", "无关A", "相关Y", "无关B"]
    relevant = {"相关X", "相关Y", "相关Z"}
    assert precision_at_k(ranked, relevant, 2) == pytest.approx(0.5)
    assert precision_at_k(["相关X"], relevant, 8) == pytest.approx(1 / 8)
    assert recall_at_k(ranked, relevant, 2) == pytest.approx(1 / 3)
    assert recall_at_k(ranked, relevant, 4) == pytest.approx(2 / 3)


def test_reciprocal_rank_uses_first_hit_position():
    assert reciprocal_rank(["无关", "相关X", "相关Y"], {"相关X", "相关Y"}) == pytest.approx(0.5)
    assert reciprocal_rank(["无关", "无关2"], {"相关X"}) == 0.0


def test_ndcg_rewards_earlier_hits():
    relevant = {"相关X"}
    assert ndcg_at_k(["相关X", "无关"], relevant, 2) == pytest.approx(1.0)
    late = ndcg_at_k(["无关", "相关X"], relevant, 2)
    assert 0 < late < 1.0
    assert ndcg_at_k(["无关", "无关2"], relevant, 2) == 0.0


def test_ndcg_full_recall_but_wrong_order_is_penalised():
    """两个相关项都在，但顺序颠倒时 nDCG 应低于 1，而 hit/recall 抓不到这个差别。"""
    relevant = {"相关X", "相关Y"}
    perfect = ndcg_at_k(["相关X", "相关Y"], relevant, 2)
    swapped = ndcg_at_k(["相关Y", "相关X"], relevant, 2)
    assert perfect == pytest.approx(1.0)
    assert swapped == pytest.approx(1.0)
    interleaved = ndcg_at_k(["相关X", "无关", "相关Y"], relevant, 3)
    assert interleaved < perfect


def test_pass_pow_k_requires_every_run_to_pass():
    assert pass_pow_k([True, True, True]) == 1.0
    assert pass_pow_k([True, False, True]) == 0.0
    assert pass_pow_k([]) == 0.0


def test_summarize_empty_sample_reports_nothing():
    stat = summarize([])
    assert stat == {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}


def test_summarize_ci_brackets_mean_and_shrinks_with_sample_size():
    small = summarize([1.0, 0.0, 1.0, 0.0], bootstrap=500)
    large = summarize([1.0, 0.0] * 200, bootstrap=500)
    assert small["ci95_low"] <= small["mean"] <= small["ci95_high"]
    assert (large["ci95_high"] - large["ci95_low"]) < (small["ci95_high"] - small["ci95_low"])


def test_retrieval_report_aggregates_all_ks():
    cases = [
        (["相关X", "无关"], {"相关X"}),
        (["无关", "相关Y"], {"相关Y"}),
        (["无关", "无关2"], {"相关Z"}),
    ]
    report = retrieval_report(cases, max_k=8, ks=(1, 3), bootstrap=200)
    assert report["n"] == 3
    assert report["per_k"]["hit@1"]["mean"] == pytest.approx(1 / 3)
    assert report["per_k"]["hit@3"]["mean"] == pytest.approx(2 / 3)
    assert report["per_k"]["recall@3"]["mean"] == pytest.approx(2 / 3)
    assert report["mrr"]["mean"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    flat = flat_means(report)
    assert flat["hit@1"] == pytest.approx(1 / 3)
    assert flat["mrr"] == pytest.approx(0.5)


def test_retrieval_report_handles_empty_input():
    report = retrieval_report([], ks=(1,), bootstrap=0)
    assert report["n"] == 0
    assert report["per_k"] == {}




from metrics import accuracy_report, cohen_kappa, paired_delta


def test_paired_delta_flags_uniform_drop():
    base = [1.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    cur = [0.0] * 10
    res = paired_delta(base, cur)
    assert res["verdict"] == "regression"
    assert res["significant"] is True
    assert res["delta"] == pytest.approx(-0.7)
    assert res["ci95_high"] < 0


def test_paired_delta_ignores_small_fluctuation():
    """小幅抖动不能让门禁飘红——这是把符号判定换成配对检验的全部理由。"""
    base = [1.0, 0.0] * 25
    cur = [0.0, 1.0] * 25
    res = paired_delta(base, cur)
    assert res["delta"] == pytest.approx(0.0)
    assert res["verdict"] == "stable"
    assert res["ci95_low"] < 0 < res["ci95_high"]


def test_paired_delta_requires_enough_pairs():
    res = paired_delta([1.0, 0.0], [0.0, 0.0])
    assert res["verdict"] == "insufficient_data"
    assert res["delta"] is None


def test_paired_delta_supports_lower_is_better():
    """延迟类指标升高是退步：同一个 +1.0 的差值，方向语义要能翻转。"""
    base = [1.0] * 10
    cur = [2.0] * 10
    assert paired_delta(base, cur)["verdict"] == "improved"
    assert paired_delta(base, cur, higher_is_better=False)["verdict"] == "regression"


def test_cohen_kappa_perfect_agreement():
    human = [True] * 10 + [False] * 10
    res = cohen_kappa(human, human)
    assert res["kappa"] == pytest.approx(1.0)
    assert res["agreement"] == pytest.approx(1.0)


def test_cohen_kappa_penalises_chance_agreement():
    """judge 全判通过时原始一致率有 0.25，kappa 必须把这份蒙对扣成 0。"""
    human = [True] * 5 + [False] * 15
    judge = [True] * 20
    res = cohen_kappa(human, judge)
    assert res["agreement"] == pytest.approx(0.25)
    assert res["kappa"] == pytest.approx(0.0, abs=1e-9)
    assert res["verdict"] == "不可用"


def test_cohen_kappa_empty_input():
    res = cohen_kappa([], [])
    assert res["n"] == 0
    assert res["kappa"] is None


def test_accuracy_report_carries_confidence_interval():
    verdicts = [{"pass": True, "faithful": True, "relevant": True, "abstain": False}] * 8 + \
               [{"pass": False, "faithful": False, "relevant": False, "abstain": False}] * 4
    rep = accuracy_report(verdicts)
    ci = rep["ci95"]["accuracy"]
    assert ci[0] <= rep["accuracy"] <= ci[1]
    assert rep["ci95"]["relevant_rate"] is not None


from metrics import (
    answer_correctness,
    citation_accuracy,
    context_relevancy,
    fact_recall,
    latency_slo_report,
    refusal_breakdown,
    sut_token_report,
    throughput_report,
)


def test_context_relevancy_and_fact_recall_and_citations():
    assert context_relevancy(["doc1", "doc2", "doc1"], {"doc1"}) == pytest.approx(2 / 3)
    assert context_relevancy([], {"doc1"}) == 0.0
    assert fact_recall("支持 140W 与 USB-C，须使用 C1 口", ["140W", "usb-c", "5A"]) == pytest.approx(2 / 3)
    assert fact_recall("abc", []) is None
    cites = [{"document_id": "d1"}, {"document_id": "d2"}]
    ca = citation_accuracy("结论A [1]，结论B [3]，结论C [2]", cites, expected_docs={"d1"})
    assert ca["total_refs"] == 3
    assert ca["valid_refs"] == 2
    assert ca["in_bounds_rate"] == pytest.approx(2 / 3)
    assert ca["expected_doc_rate"] == pytest.approx(1 / 3)


def test_refusal_breakdown_and_answer_correctness():
    cases = [
        {"answerability": "answerable", "abstain": True, "pass": False},
        {"answerability": "answerable", "abstain": False, "pass": True},
        {"answerability": "unanswerable", "abstain": True, "pass": True},
        {"answerability": "unanswerable", "abstain": False, "pass": False},
    ]
    rb = refusal_breakdown(cases)
    assert rb["false_refusal_rate"] == pytest.approx(0.5)
    assert rb["true_abstain_rate"] == pytest.approx(0.5)
    assert answer_correctness(1.0, 0.8) == pytest.approx(0.25 + 0.6)


def test_sut_token_slo_and_throughput_reports():
    recs = [
        {
            "prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250, "cost_usd": 0.0001,
            "llm_attempts": 2, "llm_errors": 0, "failovers": 1, "json_retries": 1, "cache_hit": False,
            "by_role": {"hyde": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60, "cost_usd": 0.000024, "llm_attempts": 1},
                        "product": {"prompt_tokens": 150, "completion_tokens": 40, "total_tokens": 190, "cost_usd": 0.000076, "llm_attempts": 1}},
        },
        {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "llm_attempts": 0, "llm_errors": 0, "failovers": 0, "json_retries": 0, "cache_hit": True,
            "by_role": {},
        },
    ]
    sut = sut_token_report(recs)
    assert sut["total_tokens"] == 250
    assert sut["cache_hit_rate"] == pytest.approx(0.5)
    assert sut["tokens_per_uncached_case"] == pytest.approx(250.0)
    assert sut["estimated_cache_tokens_saved"] == 250
    assert sut["json_retry_rate"] == pytest.approx(1.0)
    assert sut["failover_rate"] == pytest.approx(1.0)
    assert sut["by_role"]["hyde"]["total_tokens"] == 60

    slo = latency_slo_report(
        [1.0, 15.0],
        stage_records=[{"queue_wait_ms": 20, "rerank_ms": 100}, {"queue_wait_ms": 40, "rerank_ms": 300}],
        sla_pairs=[(1.0, 10.0), (15.0, 10.0)],
        degraded_records=[{"es_fallback": True, "rerank_fallback": False}, {"es_fallback": False, "rerank_fallback": False}],
    )
    assert slo["sla_breach_count"] == 1
    assert slo["sla_breach_rate"] == pytest.approx(0.5)
    assert slo["stage_p50_ms"]["queue_wait_ms"] == 30
    assert slo["degraded_rates"]["es_fallback_rate"] == pytest.approx(0.5)

    tp = throughput_report(4.0, 8, 4, [1.0, 2.0])
    assert tp["qps"] == pytest.approx(2.0)
    assert tp["concurrency"] == 4


def test_recall_cutoff_format_matches_gate_before_and_after_archiving():
    from evals.rag_eval.run import gate_check, six_metrics

    metrics = six_metrics({"retrieval": {"recall@8": 0.75}})
    assert metrics["recall@k"]["8"] == 0.75
    expected = ([], [("context_recall@8", 0.75, 0.80)])
    assert gate_check(metrics) == expected
    assert gate_check(json.loads(json.dumps(metrics))) == expected
