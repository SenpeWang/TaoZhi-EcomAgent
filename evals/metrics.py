#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估指标实现：检索排序指标、把握度指标与自举置信区间。

为什么单独成模块：指标口径一旦散落在各脚本里，就会出现"同一个 Hit@3 在两个脚本里算法不同"
的情况，基线对比就失去意义。所有评估脚本只从这里取指标。

口径约定：
- 相关性是二值的（relevance ∈ {0,1}）：候选是否命中预期标题集合。
- 六项核心指标：Recall@K、MRR、NDCG@K（本模块）；答案准确率、Token 成本、
  延迟（run_evals.py 汇总，成本口径统一取自 observability.metrics.token_usage）。
- 命中判定用集合包含，不做模糊匹配——宁可低估召回，也不要虚高。
- 平均指标一律给出自举置信区间；样本量小的时候点估计没有决策价值。
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

DEFAULT_BOOTSTRAP = 1000
DEFAULT_SEED = 20261016


def hit_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """前 k 条里是否至少命中一条相关项（0/1）。"""
    exp = set(relevant)
    if not exp:
        return 0.0
    return 1.0 if any(t in exp for t in ranked[:k]) else 0.0


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """前 k 条中的相关项占比。"""
    if k <= 0:
        return 0.0
    exp = set(relevant)
    top = ranked[:k]
    if not top:
        return 0.0
    return sum(1 for t in top if t in exp) / len(top)


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """前 k 条覆盖了全部相关项的比例。"""
    exp = set(relevant)
    if not exp:
        return 0.0
    got = {t for t in ranked[:k] if t in exp}
    return len(got) / len(exp)


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    """第一条相关项的倒数排名；无命中返回 0。"""
    exp = set(relevant)
    for i, t in enumerate(ranked):
        if t in exp:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """二值相关性的 nDCG@k：排序越靠前越好，同时惩罚相关项被埋没。"""
    exp = set(relevant)
    if not exp:
        return 0.0
    dcg = sum(1.0 / np.log2(i + 2) for i, t in enumerate(ranked[:k]) if t in exp)
    ideal = sum(1.0 / np.log2(i + 2) for i in range(min(len(exp), k)))
    return float(dcg / ideal) if ideal > 0 else 0.0


def pass_pow_k(runs: Sequence[bool]) -> float:
    """pass^k：同一用例跑 k 次是否全部通过（非确定性系统必须看这个）。

    输入是 k 次独立运行的结果，返回 1.0 表示 k 次全过；任何一次失败即为 0.0。
    单次通过率（mean）会系统性高估稳定性，两个数要一起报。
    """
    return 1.0 if runs and all(runs) else 0.0


def summarize(values: Sequence[float], bootstrap: int = DEFAULT_BOOTSTRAP,
              seed: int = DEFAULT_SEED) -> dict:
    """点估计 + 自举置信区间。样本为空时返回 n=0，不编造数字。

    保留完整精度，取整交给渲染层——在计算层取整会让基线对比出现假的"无变化"。
    """
    arr = np.asarray([v for v in values], dtype="float64")
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    mean = float(arr.mean())
    if n == 1 or bootstrap <= 0:
        return {"n": n, "mean": mean, "ci95_low": None, "ci95_high": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(bootstrap, n))
    means = arr[idx].mean(axis=1)
    return {"n": n, "mean": mean,
            "ci95_low": float(np.percentile(means, 2.5)),
            "ci95_high": float(np.percentile(means, 97.5))}


def retrieval_report(cases: Sequence[dict], max_k: int = 8, ks: Sequence[int] = (1, 3, 8),
                     bootstrap: int = DEFAULT_BOOTSTRAP) -> dict:
    """给定 [(ranked_titles, expected_titles)] 计算整套检索指标。

    返回每个 k 的 hit / precision / recall / ndcg，外加 MRR 与各自置信区间。
    """
    out: dict = {"n": len(cases), "ks": list(ks), "per_k": {}}
    if not cases:
        return out
    for k in ks:
        if k > max_k:
            continue
        out["per_k"][f"hit@{k}"] = summarize(
            [hit_at_k(r, e, k) for r, e in cases], bootstrap)
        out["per_k"][f"precision@{k}"] = summarize(
            [precision_at_k(r, e, k) for r, e in cases], bootstrap)
        out["per_k"][f"recall@{k}"] = summarize(
            [recall_at_k(r, e, k) for r, e in cases], bootstrap)
        out["per_k"][f"ndcg@{k}"] = summarize(
            [ndcg_at_k(r, e, k) for r, e in cases], bootstrap)
    out["mrr"] = summarize([reciprocal_rank(r, e) for r, e in cases], bootstrap)
    return out


def flat_means(report: dict) -> dict:
    """把 retrieval_report 压平成 {指标名: 均值}，供基线对比使用。"""
    flat = {}
    for name, stat in (report.get("per_k") or {}).items():
        flat[name] = stat["mean"]
    if report.get("mrr"):
        flat["mrr"] = report["mrr"]["mean"]
    return {k: v for k, v in flat.items() if v is not None}


def accuracy_report(verdicts: Sequence[dict]) -> dict:
    """答案准确率：LLM-as-judge 的判定汇总。

    accuracy = 通过率（含正确拒答）；另报忠实度、切题度、正确拒答率三个分项——
    只有总通过率会掩盖"答了但编造"和"该拒答却硬答"两类问题。
    """
    n = len(verdicts)
    if not n:
        return {"n": 0, "accuracy": None, "grounded_rate": None,
                "relevant_rate": None, "abstain_rate": None}
    acc = sum(1 for v in verdicts if v.get("pass")) / n
    grounded = sum(1 for v in verdicts if v.get("faithful")) / n
    relevant = sum(1 for v in verdicts if v.get("relevant")) / n
    abstain = sum(1 for v in verdicts if v.get("abstain")) / n
    return {"n": n, "accuracy": acc, "grounded_rate": grounded,
            "relevant_rate": relevant, "abstain_rate": abstain}


def cost_report(before: dict, after: dict, n: int = 0) -> dict:
    """两次 token_usage() 快照之差 → 本次评估的 token 消耗与估算成本。

    差值取 max(0, …) 防御计数器被 reset 的情况；n>0 才给单题均值（否则不编造）。
    """
    d_prompt = max(0.0, float(after.get("prompt_tokens", 0.0)) - float(before.get("prompt_tokens", 0.0)))
    d_comp = max(0.0, float(after.get("completion_tokens", 0.0)) - float(before.get("completion_tokens", 0.0)))
    d_cost = max(0.0, float(after.get("cost_usd", 0.0)) - float(before.get("cost_usd", 0.0)))
    total = d_prompt + d_comp
    out = {"prompt_tokens": int(d_prompt), "completion_tokens": int(d_comp),
           "total_tokens": int(total), "cost_usd": round(d_cost, 6)}
    if n > 0:
        out["tokens_per_case"] = round(total / n, 1)
        out["cost_usd_per_case"] = round(d_cost / n, 6)
    return out


def latency_report(seconds: Sequence[float]) -> dict:
    """延迟分布：p50 / p95 / 均值（秒）。空样本不编造。"""
    arr = np.asarray([float(x) for x in seconds], dtype="float64")
    if arr.size == 0:
        return {"n": 0, "p50_s": None, "p95_s": None, "mean_s": None}
    return {"n": int(arr.size), "p50_s": float(np.percentile(arr, 50)),
            "p95_s": float(np.percentile(arr, 95)), "mean_s": float(arr.mean())}
