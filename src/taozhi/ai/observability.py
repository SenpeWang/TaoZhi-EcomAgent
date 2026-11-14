"""轻量指标采集（PRD N3）：Agent 成功率 / RAG 命中率 / LLM 成本 / 耗时。"""

from __future__ import annotations

import threading
from typing import Dict, List

_lock = threading.RLock()
_counters: Dict[str, float] = {}
_timers: Dict[str, List[float]] = {}

# 每 1K token 的估算单价（美元），用于成本看板
_PRICE_PER_1K = {"fast": 0.0004, "strong": 0.006, "vision": 0.003}


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + value


def observe(name: str, seconds: float) -> None:
    with _lock:
        values = _timers.setdefault(name, [])
        values.append(seconds)
        if len(values) > 2000:
            del values[:-2000]


def record_llm_call(tier: str, seconds: float, prompt_tokens: int, completion_tokens: int) -> None:
    inc(f"llm.calls.{tier}")
    inc("llm.calls.total")
    inc(f"llm.tokens.prompt.{tier}", prompt_tokens)
    inc(f"llm.tokens.completion.{tier}", completion_tokens)
    observe(f"llm.latency.{tier}", seconds)


def record_llm_error(tier: str) -> None:
    inc(f"llm.errors.{tier}")
    inc("llm.errors.total")


def percentile(name: str, p: float = 0.95) -> float:
    with _lock:
        values = sorted(_timers.get(name, []))
    if not values:
        return 0.0
    idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[idx]


def rag_hit_rate(channel: str = "") -> float:
    with _lock:
        if channel:
            calls = _counters.get(f"rag.calls.{channel}", 0.0)
            empty = _counters.get(f"rag.empty.{channel}", 0.0)
            return 0.0 if not calls else (calls - empty) / calls
        calls = sum(v for k, v in _counters.items() if k.startswith("rag.calls."))
        empty = sum(v for k, v in _counters.items() if k.startswith("rag.empty."))
    return 0.0 if not calls else (calls - empty) / calls


def snapshot() -> Dict[str, float]:
    with _lock:
        out = dict(_counters)
    out["llm.error_rate"] = out.get("llm.errors.total", 0.0) / max(1.0, out.get("llm.calls.total", 1.0))
    out["rag.hit_rate"] = rag_hit_rate()
    return out


def token_usage() -> Dict[str, float]:
    """累计 token 用量与估算成本（美元），口径与 _PRICE_PER_1K 同源。

    评测与成本看板都必须读这里，禁止各自按自己的单价重算——
    否则同一批调用会算出两个成本。缺失 tier 的用量按 0 计，不编造。
    """
    with _lock:
        counters = dict(_counters)
    prompt = comp = cost = 0.0
    for tier, price in _PRICE_PER_1K.items():
        p_tok = counters.get(f"llm.tokens.prompt.{tier}", 0.0)
        c_tok = counters.get(f"llm.tokens.completion.{tier}", 0.0)
        prompt += p_tok
        comp += c_tok
        cost += (p_tok + c_tok) / 1000.0 * price
    return {"prompt_tokens": prompt, "completion_tokens": comp,
            "total_tokens": prompt + comp, "cost_usd": cost}


def reset() -> None:
    with _lock:
        _counters.clear()
        _timers.clear()
