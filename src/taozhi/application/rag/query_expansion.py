"""按问答外发许可和调用预算生成 HyDE 检索向量。"""
from __future__ import annotations

import time
from contextlib import closing
from typing import TYPE_CHECKING, Any

from taozhi.config import Config, load_config
from taozhi.domain.entities import Principal
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence.job_repo import count_model_calls, insert_model_call, update_model_call
from taozhi.observability.tracing import record_diagnostic

if TYPE_CHECKING:
    import numpy as np


_HYDE_SYSTEM = (
    "你是商品资料检索助手。根据用户问题，写一段可能出现在商品资料库中的正文段落："
    "陈述句、含型号、参数或步骤等具体细节。允许内容与事实不符，禁止任何解释、前言或标题，只输出段落正文。"
)


def build_hyde_vector(
    query: str,
    *,
    conn: Any = None,
    principal: Principal | None = None,
    job: dict[str, Any] | None = None,
    trace_out: dict[str, Any] | None = None,
) -> np.ndarray | None:
    """脱敏后生成假设性答案并编码为检索向量（生产与离线评测共用同一口径）。"""
    trace: dict[str, Any] = {
        "used": False,
        "elapsed_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "llm_attempts": 0,
        "failover_count": 0,
        "outcome": "skipped",
    }
    try:
        cfg = load_config()
        if not cfg.hyde_enabled:
            return None
        if job is not None:
            if not (job.get("payload") or {}).get("allow_external", True) or int(job.get("input_level", 1)) >= 3:
                return None
            if conn is not None:
                with conn.transaction():
                    if count_model_calls(conn, job["id"]) >= cfg.max_model_calls:
                        return None
        from taozhi.ai.client import LLMClient, ModelTier
        from taozhi.ai.observability import usage_scope
        from taozhi.infrastructure.knowledge.embedding import available as embed_available, encode_passages

        if not embed_available():
            return None
        started = time.perf_counter()
        outcome = "ok"
        text = ""
        hyde_max_tok = int(getattr(cfg, "hyde_max_tokens", Config.hyde_max_tokens))
        hyde_chars = int(getattr(cfg, "hyde_passage_chars", Config.hyde_passage_chars))
        with usage_scope() as hyde_usage:
            try:
                with closing(LLMClient(allow_overseas=False)) as client:
                    text = client.chat(redact(query), tier=ModelTier.FAST, system=_HYDE_SYSTEM, max_tokens=hyde_max_tok)
            except Exception:
                outcome = "failed"
                raise
            finally:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                usage = hyde_usage.to_dict()
                trace.update(
                    used=outcome == "ok",
                    elapsed_ms=elapsed_ms,
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    total_tokens=usage["total_tokens"],
                    cost_usd=usage["cost_usd"],
                    llm_attempts=usage["llm_attempts"],
                    failover_count=usage["failover_count"],
                    outcome=outcome,
                )
                if conn is not None and principal is not None and job is not None:
                    try:
                        with conn.transaction():
                            call_id = insert_model_call(conn, principal.tenant_id, job["id"], "hyde")
                            if call_id is not None:
                                update_model_call(conn, call_id, elapsed_ms, usage["prompt_tokens"], usage["completion_tokens"], outcome)
                    except Exception as exc:
                        record_diagnostic("storage.write", "skipped", exc)
        if not text or len(text.strip()) < 20:
            trace["outcome"] = "short_or_empty"
            return None
        return encode_passages([text.strip()[:hyde_chars]])[0]
    except Exception as exc:
        record_diagnostic("retrieval.hyde", "skipped", exc)
        return None
    finally:
        if trace_out is not None:
            trace_out.update(trace)
