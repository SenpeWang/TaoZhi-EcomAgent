"""受控科研工具结构化执行器（Infrastructure Layer）。

将公网检索、网页深抓、1688 货源与 Amazon 对标工具统一包装为结构化 ToolOutcome，
显式区分 ok / empty / error / timeout / cancelled，透传取消信号与剩余超时预算，
并对返回给智能体上下文的文本实施行边界安全截断。
"""
from __future__ import annotations

import inspect
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

import httpx

from .truncate import truncate_text

ToolStatus = Literal["ok", "empty", "error", "timeout", "cancelled"]


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行的客观结构化回执。"""

    tool_name: str
    status: ToolStatus
    items: List[Dict[str, Any]] = field(default_factory=list)
    text_preview: str = ""
    data_count: int = 0
    elapsed: float = 0.0
    error_reason: str = ""


def _accepts_timeout(fn: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(fn)
        return "timeout" in sig.parameters
    except (TypeError, ValueError):
        return False


def execute_tool_with_outcome(
    tool_name: str,
    fn: Callable[..., Any],
    kwargs: Dict[str, Any],
    *,
    remaining_seconds: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
    preview_chars: int = 3200,
) -> ToolOutcome:
    """在统一超时预算与取消事件受控下运行工具并返回客观 ToolOutcome。"""
    if cancel_event is not None and cancel_event.is_set():
        return ToolOutcome(
            tool_name=tool_name,
            status="cancelled",
            error_reason="任务已被取消，跳过工具执行",
        )

    if remaining_seconds is not None and remaining_seconds <= 0:
        return ToolOutcome(
            tool_name=tool_name,
            status="timeout",
            error_reason="全局剩余时间预算耗尽，跳过工具执行",
        )

    call_kwargs = dict(kwargs)
    if remaining_seconds is not None and _accepts_timeout(fn):
        requested = call_kwargs.get("timeout")
        bounded = max(0.5, float(remaining_seconds))
        call_kwargs["timeout"] = min(float(requested), bounded) if requested is not None else min(30.0, bounded)

    t0 = time.perf_counter()
    try:
        if remaining_seconds is not None:
            wait_limit = max(0.5, float(remaining_seconds))
            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(fn, **call_kwargs)
            try:
                raw = fut.result(timeout=wait_limit)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        else:
            raw = fn(**call_kwargs)
    except (TimeoutError, FuturesTimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException) as exc:
        return ToolOutcome(
            tool_name=tool_name,
            status="timeout",
            elapsed=round(time.perf_counter() - t0, 2),
            error_reason=f"工具执行超时: {exc}",
        )
    except Exception as exc:
        return ToolOutcome(
            tool_name=tool_name,
            status="error",
            elapsed=round(time.perf_counter() - t0, 2),
            error_reason=f"{type(exc).__name__}: {exc}",
        )

    elapsed = round(time.perf_counter() - t0, 2)
    if cancel_event is not None and cancel_event.is_set():
        return ToolOutcome(
            tool_name=tool_name,
            status="cancelled",
            elapsed=elapsed,
            error_reason="工具执行期间收到任务取消请求",
        )

    if isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
        if not items and raw:
            items = [{"value": str(x)} for x in raw]
        status: ToolStatus = "ok" if items else "empty"
        serialized = json.dumps(items[:10], ensure_ascii=False)
        preview = truncate_text(serialized, max_bytes=preview_chars).content
        return ToolOutcome(
            tool_name=tool_name,
            status=status,
            items=items,
            text_preview=preview,
            data_count=len(items),
            elapsed=elapsed,
        )

    if isinstance(raw, dict):
        if raw.get("error"):
            return ToolOutcome(
                tool_name=tool_name,
                status="error",
                items=[raw],
                text_preview=truncate_text(json.dumps(raw, ensure_ascii=False), max_bytes=preview_chars).content,
                data_count=0,
                elapsed=elapsed,
                error_reason=str(raw.get("error")),
            )
        content = str(raw.get("content") or raw.get("text") or "")
        status = "ok" if (content.strip() or len(raw) > 0) else "empty"
        preview = truncate_text(json.dumps(raw, ensure_ascii=False), max_bytes=preview_chars).content
        return ToolOutcome(
            tool_name=tool_name,
            status=status,
            items=[raw] if status == "ok" else [],
            text_preview=preview,
            data_count=1 if status == "ok" else 0,
            elapsed=elapsed,
        )

    return ToolOutcome(
        tool_name=tool_name,
        status="empty",
        elapsed=elapsed,
    )
