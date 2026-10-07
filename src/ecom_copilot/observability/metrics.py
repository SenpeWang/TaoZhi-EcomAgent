"""轻量指标采集（PRD N3）：Agent 成功率 / RAG 命中率 / LLM 成本 / 耗时。"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict, List

_lock = threading.RLock()
_counters: Dict[str, float] = {}
_timers: Dict[str, List[float]] = {}
_events: List[dict] = []

# 每 1K token 的估算单价（美元），用于成本看板
_PRICE_PER_1K = {"fast": 0.0004, "strong": 0.006, "vision": 0.003}


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + value


def observe(name: str, seconds: float) -> None:
    with _lock:
        values=_timers.setdefault(name, [])
        values.append(seconds)
        if len(values)>2000: del values[:-2000]


def log_event(name: str, **fields) -> None:
    with _lock:
        record = {"event": name, "ts": time.time(), **fields}
        _events.append(record)
        if len(_events) > 2000:
            del _events[: len(_events) - 2000]


def record_llm_call(tier: str, seconds: float, prompt_tokens: int, completion_tokens: int) -> None:
    inc(f"llm.calls.{tier}")
    inc("llm.calls.total")
    inc(f"llm.tokens.prompt.{tier}", prompt_tokens)
    inc(f"llm.tokens.completion.{tier}", completion_tokens)
    observe(f"llm.latency.{tier}", seconds)


def record_llm_error(tier: str) -> None:
    inc(f"llm.errors.{tier}")
    inc("llm.errors.total")


def record_agent(agent: str, seconds: float, ok: bool = True) -> None:
    inc(f"agent.calls.{agent}")
    inc(f"agent.{'success' if ok else 'failure'}.{agent}")
    observe(f"agent.latency.{agent}", seconds)


def record_retrieval(channel: str, hits: int, requested: int) -> None:
    inc(f"rag.calls.{channel}")
    inc(f"rag.hits.{channel}", hits)
    if requested and hits == 0:
        inc(f"rag.empty.{channel}")


@contextmanager
def timer(name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - start)


def percentile(name: str, p: float = 0.95) -> float:
    with _lock:
        values = sorted(_timers.get(name, []))
    if not values:
        return 0.0
    idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[idx]


def agent_success_rate(agent: str) -> float:
    with _lock:
        calls = _counters.get(f"agent.calls.{agent}", 0.0)
        ok = _counters.get(f"agent.success.{agent}", 0.0)
    return ok / calls if calls else 1.0


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


def recent_events(limit: int = 100) -> List[dict]:
    with _lock:
        return list(_events[-limit:])


def reset() -> None:
    with _lock:
        _counters.clear()
        _timers.clear()
        _events.clear()
