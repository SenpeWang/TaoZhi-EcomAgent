"""轻量指标采集（PRD N3）：Agent 成功率 / RAG 命中率 / LLM 成本 / 耗时。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import threading
from typing import Any, Dict, Iterator, List, Optional

_lock = threading.RLock()
_counters: Dict[str, float] = {}
_timers: Dict[str, List[float]] = {}

# 每 1K token 的估算单价（美元），支持 ECOM_LLM_PRICE_{FAST|STRONG|VISION}_1K 覆盖
_PRICE_PER_1K = {"fast": 0.0004, "strong": 0.006, "vision": 0.003}


def price_per_1k(tier: str) -> float:
    """获取指定 tier 的每 1K token 单价，优先读环境变量，未设则用唯一基准表。"""
    key = f"ECOM_LLM_PRICE_{str(tier).upper()}_1K"
    raw = os.getenv(key, "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _PRICE_PER_1K.get(tier, 0.0)


def estimate_cost_usd(tier: str, prompt_tokens: int, completion_tokens: int) -> float:
    """统一口径的模型用量美元估算（evals 与 observability 共用唯一实现）。"""
    tot = max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))
    return tot / 1000.0 * price_per_1k(tier)


@dataclass
class UsageAccumulator:
    """线程/协程作用域内的单次业务请求 LLM 用量与容错事件累计器。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    llm_attempts: int = 0
    llm_errors: int = 0
    failover_count: int = 0
    json_parse_attempts: int = 0
    json_retries: int = 0
    by_tier: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def add_call(
        self,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        failover: bool = False,
    ) -> None:
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        tot = pt + ct
        cost = estimate_cost_usd(tier, pt, ct)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.total_tokens += tot
        self.cost_usd += cost
        self.llm_attempts += 1
        if failover:
            self.failover_count += 1
        bucket = self.by_tier.setdefault(tier, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0})
        bucket["prompt_tokens"] += pt
        bucket["completion_tokens"] += ct
        bucket["calls"] += 1
        bucket["cost_usd"] += cost

    def add_error(self) -> None:
        self.llm_errors += 1

    def add_json_attempt(self, *, is_retry: bool = False) -> None:
        self.json_parse_attempts += 1
        if is_retry:
            self.json_retries += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": int(self.prompt_tokens),
            "completion_tokens": int(self.completion_tokens),
            "total_tokens": int(self.total_tokens),
            "cost_usd": round(float(self.cost_usd), 6),
            "llm_attempts": int(self.llm_attempts),
            "llm_errors": int(self.llm_errors),
            "failover_count": int(self.failover_count),
            "json_parse_attempts": int(self.json_parse_attempts),
            "json_retries": int(self.json_retries),
        }


_ACTIVE_USAGE: ContextVar[Optional[UsageAccumulator]] = ContextVar("taozhi_active_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[UsageAccumulator]:
    """在当前任务/线程上下文挂载独立用量累计器，隔离并发智能体统计。"""
    acc = UsageAccumulator()
    tok = _ACTIVE_USAGE.set(acc)
    try:
        yield acc
    finally:
        _ACTIVE_USAGE.reset(tok)


def get_active_usage() -> Optional[UsageAccumulator]:
    """返回当前上下文激活的用量累计器（未挂载时为 None）。"""
    return _ACTIVE_USAGE.get()


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + value


def observe(name: str, seconds: float) -> None:
    with _lock:
        values = _timers.setdefault(name, [])
        values.append(seconds)
        if len(values) > 2000:
            del values[:-2000]


def record_llm_call(
    tier: str,
    seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    failover: bool = False,
) -> None:
    inc(f"llm.calls.{tier}")
    inc("llm.calls.total")
    inc(f"llm.tokens.prompt.{tier}", prompt_tokens)
    inc(f"llm.tokens.completion.{tier}", completion_tokens)
    if failover:
        inc("llm.failovers.total")
    observe(f"llm.latency.{tier}", seconds)
    acc = _ACTIVE_USAGE.get()
    if acc is not None:
        acc.add_call(tier, prompt_tokens, completion_tokens, failover=failover)


def record_llm_error(tier: str) -> None:
    inc(f"llm.errors.{tier}")
    inc("llm.errors.total")
    acc = _ACTIVE_USAGE.get()
    if acc is not None:
        acc.add_error()


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
    for tier in _PRICE_PER_1K:
        p_tok = counters.get(f"llm.tokens.prompt.{tier}", 0.0)
        c_tok = counters.get(f"llm.tokens.completion.{tier}", 0.0)
        prompt += p_tok
        comp += c_tok
        cost += estimate_cost_usd(tier, int(p_tok), int(c_tok))
    return {"prompt_tokens": prompt, "completion_tokens": comp,
            "total_tokens": prompt + comp, "cost_usd": cost}


def reset() -> None:
    with _lock:
        _counters.clear()
        _timers.clear()
