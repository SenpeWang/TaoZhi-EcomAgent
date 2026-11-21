"""受控科研工具结构化执行器（Infrastructure Layer）。

将公网检索、网页深抓、1688 货源、Amazon 对标及 MCP 工具统一包装为结构化 ToolOutcome，
显式区分 ok / empty / error / timeout / cancelled，使用有界共享线程池执行限时调度，
并对返回给智能体上下文的文本实施行边界安全截断。
"""
from __future__ import annotations

import atexit
import inspect
import json
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

import httpx
from opentelemetry import context as otel_context

from taozhi.domain.trace_diagnostic import CATALOG
from .truncate import truncate_text

_LOG = logging.getLogger(__name__)
ToolStatus = Literal["ok", "empty", "error", "timeout", "cancelled"]

_POOL_LOCK = threading.Lock()
_TOOL_POOL: Optional[ThreadPoolExecutor] = None


class ToolInvocationError(RuntimeError):
    """采集通道不可用或协议错误，仅携带可展示的内部故障码。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(CATALOG[code][1])


def result_items(payload: object, field: str, limit: int) -> list:
    """读取真实采集结果，区分有效的零条记录和格式异常。"""
    items = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ToolInvocationError("tool.invalid_response")
    return items[:limit]


def _get_tool_pool() -> ThreadPoolExecutor:
    """懒加载并复用全局有界工具执行线程池，避免频繁建删线程。"""
    global _TOOL_POOL
    with _POOL_LOCK:
        if _TOOL_POOL is None:
            _TOOL_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="taozhi-tool")
        return _TOOL_POOL


def _shutdown_tool_pool() -> None:
    """进程退出时非阻塞关闭共享工具线程池。"""
    global _TOOL_POOL
    with _POOL_LOCK:
        if _TOOL_POOL is not None:
            try:
                _TOOL_POOL.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                _LOG.debug("关闭共享工具线程池异常: %s", exc)
            _TOOL_POOL = None


atexit.register(_shutdown_tool_pool)


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行的客观结构化回执。"""

    tool_name: str
    status: ToolStatus
    dispatched: bool
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


def clamp_timeout_args(
    args: Dict[str, Any],
    *,
    remaining_seconds: Optional[float],
    inject_timeout: bool = True,
) -> Dict[str, Any]:
    """将请求参数中的超时字段裁剪至剩余全局时间预算范围内。

    Args:
        args: 原始工具入参字典。
        remaining_seconds: 当前任务剩余全局秒数预算。
        inject_timeout: 若目标可调用对象接收 timeout 参数则写入裁剪后预算。

    Returns:
        裁剪后的参数副本字典。
    """
    call_args = dict(args)
    if remaining_seconds is not None and inject_timeout:
        bounded = max(0.5, float(remaining_seconds))
        requested = call_args.get("timeout")
        call_args["timeout"] = min(float(requested), bounded) if requested is not None else min(30.0, bounded)
    return call_args


def normalize_tool_result(
    tool_name: str,
    raw: Any,
    elapsed: float,
    *,
    preview_chars: int = 3200,
) -> ToolOutcome:
    """将原始工具返回值归一化为结构化 ToolOutcome。

    Args:
        tool_name: 工具名称。
        raw: 工具原始返回对象（list / dict / 标量）。
        elapsed: 实际执行耗时（秒）。
        preview_chars: 摘要预览字符上限。

    Returns:
        归一化后的 ToolOutcome 实例。
    """
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
            dispatched=True,
            items=items,
            text_preview=preview,
            data_count=len(items),
            elapsed=elapsed,
        )

    if isinstance(raw, dict):
        if raw.get("error") or raw.get("status") == "error":
            err_msg = str(raw.get("error") or raw.get("text") or "工具返回错误")
            return ToolOutcome(
                tool_name=tool_name,
                status="error",
                dispatched=True,
                items=[raw],
                text_preview=truncate_text(json.dumps(raw, ensure_ascii=False), max_bytes=preview_chars).content,
                data_count=0,
                elapsed=elapsed,
                error_reason=err_msg,
            )
        content = str(raw.get("content") or raw.get("text") or "")
        status = "ok" if (content.strip() or len(raw) > 0) else "empty"
        preview = truncate_text(json.dumps(raw, ensure_ascii=False), max_bytes=preview_chars).content
        return ToolOutcome(
            tool_name=tool_name,
            status=status,
            dispatched=True,
            items=[raw] if status == "ok" else [],
            text_preview=preview,
            data_count=1 if status == "ok" else 0,
            elapsed=elapsed,
        )

    return ToolOutcome(tool_name=tool_name, status="empty", dispatched=True, elapsed=elapsed)


def execute_tool_with_outcome(
    tool_name: str,
    fn: Callable[..., Any],
    kwargs: Dict[str, Any],
    *,
    remaining_seconds: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
    preview_chars: int = 3200,
) -> ToolOutcome:
    """在统一超时预算与取消事件受控下运行工具并返回客观 ToolOutcome。

    Args:
        tool_name: 工具名称。
        fn: 待调用工具函数。
        kwargs: 工具调用关键字参数。
        remaining_seconds: 剩余可用总秒数；为 None 时不做额外线程池等待截断。
        cancel_event: 任务级取消事件信号。
        preview_chars: 输出文本截断字节/字符上限。

    Returns:
        包含状态、结果摘要与耗时的 ToolOutcome。
    """
    if cancel_event is not None and cancel_event.is_set():
        return ToolOutcome(tool_name=tool_name, status="cancelled", dispatched=False, error_reason="任务已被取消，跳过工具执行")

    if remaining_seconds is not None and remaining_seconds <= 0:
        return ToolOutcome(tool_name=tool_name, status="timeout", dispatched=False, error_reason="全局剩余时间预算耗尽，跳过工具执行")

    call_kwargs = clamp_timeout_args(kwargs, remaining_seconds=remaining_seconds, inject_timeout=_accepts_timeout(fn))
    t0 = time.perf_counter()
    dispatched = False
    try:
        if remaining_seconds is not None:
            wait_limit = max(0.5, float(remaining_seconds))
            trace_context = otel_context.get_current()

            def invoke_in_trace():
                token = otel_context.attach(trace_context)
                try:
                    return fn(**call_kwargs)
                finally:
                    otel_context.detach(token)

            fut = _get_tool_pool().submit(invoke_in_trace)
            try:
                raw = fut.result(timeout=wait_limit)
            finally:
                if not fut.done():
                    fut.cancel()
                dispatched = not fut.cancelled()
        else:
            dispatched = True
            raw = fn(**call_kwargs)
    except (TimeoutError, FuturesTimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException) as exc:
        return ToolOutcome(
            tool_name=tool_name,
            status="timeout",
            dispatched=dispatched,
            elapsed=round(time.perf_counter() - t0, 2),
            error_reason=f"工具执行超时: {exc}",
        )
    except Exception as exc:
        return ToolOutcome(
            tool_name=tool_name,
            status="error",
            dispatched=dispatched,
            elapsed=round(time.perf_counter() - t0, 2),
            error_reason=f"{type(exc).__name__}: {exc}",
        )

    elapsed = round(time.perf_counter() - t0, 2)
    if cancel_event is not None and cancel_event.is_set():
        return ToolOutcome(
            tool_name=tool_name,
            status="cancelled",
            dispatched=dispatched,
            elapsed=elapsed,
            error_reason="工具执行期间收到任务取消请求",
        )

    return normalize_tool_result(tool_name, raw, elapsed, preview_chars=preview_chars)
