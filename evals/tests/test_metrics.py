#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估指标单元测试：口径一旦写错，所有基线和阈值决策都会跟着错，所以必须有测试盯住。

运行：.venv/bin/python -m pytest evals/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from metrics import (  # noqa: E402
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
    assert hit_at_k(ranked, set(), 3) == 0.0  # 无预期项不算命中


def test_precision_and_recall_are_not_the_same_number():
    ranked = ["相关X", "无关A", "相关Y", "无关B"]
    relevant = {"相关X", "相关Y", "相关Z"}
    assert precision_at_k(ranked, relevant, 2) == pytest.approx(0.5)
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
    assert swapped == pytest.approx(1.0)  # 二值相关性下同权，交换无差别
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
        (["相关X", "无关"], {"相关X"}),          # 完美
        (["无关", "相关Y"], {"相关Y"}),          # 第 2 位命中
        (["无关", "无关2"], {"相关Z"}),          # 全丢
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



# ─────────── 运行间比较与 judge 校准（2026-10-21 新增） ───────────

from metrics import accuracy_report, cohen_kappa, paired_delta  # noqa: E402


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
    cur = [0.0, 1.0] * 25          # 均值相同，只是逐例对调
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
    human = [True] * 5 + [False] * 15      # 25% 通过
    judge = [True] * 20                     # 全判通过
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
