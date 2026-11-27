"""Taozhi 内建任务追踪，使用 OpenTelemetry SDK 采集并在租户数据库留存。"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid4

from openinference.instrumentation import OITracer, TraceConfig
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, StatusCode

from taozhi.domain.trace_diagnostic import CATALOG, OUTCOMES, DiagnosticOutcome
from .diagnostics import EVENT_NAME, diagnostic_events, failure_code, http_status_of
from .span_attributes import safe_attributes
from .span_writer import submit_span

_LOG = logging.getLogger(__name__)
_JOB = otel_context.create_key("taozhi.job")
_SPAN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,119}")
_INSTRUMENTATION = "taozhi.pipeline"
_PROVIDER_LOCK = threading.Lock()
_provider: TracerProvider | None = None


def _enabled() -> bool:
    """判断执行链路采集是否启用（TAOZHI_TRACING 未关闭）。

    Returns:
        True 表示已启用。
    """
    return os.environ.get("TAOZHI_TRACING", "1").strip().lower() not in {"", "0", "false"}


class _TaskSpanProcessor(SpanProcessor):
    """投递当前任务的运行中及结束快照，独立评测不写入租户数据库。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = OrderedDict()

    def on_start(self, span, parent_context=None):
        """按任务上下文登记运行中的 span，并立即投递首份快照。

        Args:
            span: 新建的 span。
            parent_context: 父上下文，用于继承所属任务。
        """
        job = otel_context.get_value(_JOB, parent_context)
        if not _enabled() or (job is None and span.parent is None):
            return
        key = (span.context.trace_id, span.context.span_id)
        with self._lock:
            if span.parent:
                parent_job = self._active.get((span.context.trace_id, span.parent.span_id))
                if job is not None and parent_job is not None and parent_job != job:
                    return
                job = job or parent_job
            if job is None:
                return
            if len(self._active) >= 16384:
                self._active.popitem(last=False)
            self._active[key] = job
        self._snapshot(span, job)

    def on_end(self, span: ReadableSpan):
        """移除已结束 span 的任务绑定并投递终态快照。

        Args:
            span: 已结束的 span。
        """
        with self._lock:
            job = self._active.pop((span.context.trace_id, span.context.span_id), None)
        if job is not None:
            self._snapshot(span, job)

    def add_diagnostic(self, span, job, attrs, publish_now: bool) -> bool:
        """只向开始时绑定同一任务的活动节点追加事件。"""
        ctx = span.get_span_context()
        with self._lock:
            if self._active.get((ctx.trace_id, ctx.span_id)) != job or not span.is_recording():
                return False
            span.add_event(EVENT_NAME, {**attrs, "event_id": uuid4().hex})
        if publish_now:
            self._snapshot(span, job)
        return True

    @staticmethod
    def _snapshot(span: ReadableSpan, job: tuple) -> None:
        """把 span 与任务标识组装为可入库快照并投递后台写入。

        Args:
            span: 待快照的 span。
            job: 所属任务的三元组 (tenant_id, job_id, run_version)。
        """
        try:
            tenant_id, job_id, run_version = job
            attributes = safe_attributes(span.attributes)
            attributes["otel.span.kind"] = span.kind.name
            start, end = span.start_time, span.end_time
            status = "running" if end is None else ("error" if span.status.status_code == StatusCode.ERROR else "ok")
            if status == "error" and "error.type" not in attributes:
                for event in reversed(span.events):
                    if event.name == "exception" and event.attributes:
                        attributes.update(safe_attributes({"error.type": event.attributes.get("exception.type")}))
                        break
            scope = getattr(span.instrumentation_scope, "name", "") or ""
            if scope == _INSTRUMENTATION and _SPAN_NAME.fullmatch(span.name):
                name = span.name
            else:
                library = scope if _SPAN_NAME.fullmatch(scope) else "library"
                name = f"{library}.{span.kind.name.lower()}"
            submit_span({
                "tenant_id": tenant_id,
                "job_id": job_id,
                "run_version": run_version,
                "trace_id": trace.format_trace_id(span.context.trace_id),
                "span_id": trace.format_span_id(span.context.span_id),
                "parent_span_id": trace.format_span_id(span.parent.span_id) if span.parent else None,
                "name": name,
                "kind": attributes.pop(SpanAttributes.OPENINFERENCE_SPAN_KIND, "INTERNAL"),
                "status": status,
                "started_at": datetime.fromtimestamp(start / 1e9, timezone.utc),
                "ended_at": datetime.fromtimestamp(end / 1e9, timezone.utc) if end is not None else None,
                "duration_ms": max(0, (end - start) // 1_000_000) if end is not None else None,
                "error_type": attributes.get("error.type"),
                "attributes": attributes,
                "diagnostics": diagnostic_events(span.events) if scope == _INSTRUMENTATION else [],
            })
        except Exception as exc:
            _LOG.warning("执行链路采集失败 (%s)", type(exc).__name__)


_PROCESSOR = _TaskSpanProcessor()


def _local_provider(service_name: str = "taozhi-worker") -> TracerProvider:
    """返回进程内单例 TracerProvider 并挂载任务 Span 处理器。

    Args:
        service_name: 资源服务名。

    Returns:
        进程共享的 TracerProvider。
    """
    global _provider
    if _provider is None:
        with _PROVIDER_LOCK:
            if _provider is None:
                provider = TracerProvider(
                    resource=Resource({"service.name": service_name}),
                    sampler=ALWAYS_ON,
                    shutdown_on_exit=False,
                )
                provider.add_span_processor(_PROCESSOR)
                _provider = provider
    return _provider


def setup_tracing(service_name: str = "taozhi-worker") -> bool:
    """初始化进程内 SDK，写入线程在收到第一条任务 Span 时启动。"""
    if not _enabled():
        return False
    provider = _local_provider(service_name)
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        trace.set_tracer_provider(provider)
    return True


@lru_cache(maxsize=1)
def _workflow_tracer() -> OITracer:
    """在本地 OTel provider 上生成 AI 语义 span，过滤模型输入输出。"""
    return OITracer(
        _local_provider().get_tracer(_INSTRUMENTATION),
        config=TraceConfig(hide_inputs=True, hide_outputs=True, hide_llm_invocation_parameters=True),
    )


def record_diagnostic(
    code: str, outcome: DiagnosticOutcome, exc: BaseException | None = None, *,
    http_status: int | None = None, publish_now: bool = True,
) -> None:
    """在捕获处记录安全的中文诊断，同一事件关联进程日志、任务、链路与节点。"""
    try:
        category, message = CATALOG[code]
        span = trace.get_current_span()
        ctx = span.get_span_context()
        job = otel_context.get_value(_JOB)
        attrs = {"code": code, "outcome": outcome}
        if exc is not None:
            attrs["exception_type"] = type(exc).__name__
        status = http_status or http_status_of(exc)
        if type(status) is int and 100 <= status <= 599:
            attrs["http_status"] = status
        scope = getattr(getattr(span, "instrumentation_scope", None), "name", None)
        step = getattr(span, "name", "") if scope == _INSTRUMENTATION else ""
        step = step if _SPAN_NAME.fullmatch(step) else ""
        trace_id = trace.format_trace_id(ctx.trace_id) if ctx.is_valid else ""
        span_id = trace.format_span_id(ctx.span_id) if ctx.is_valid else ""
        _LOG.log(logging.ERROR if outcome == "failed" else logging.WARNING,
                 "%s。%s（故障码=%s，HTTP=%s，异常类型=%s，步骤=%s，链路=%s，节点=%s）",
                 message, OUTCOMES[outcome], code, attrs.get("http_status", "无"), attrs.get("exception_type", "无"),
                 step or "未采集", trace_id or "未采集", span_id or "未采集",
                 extra={"diagnostic_code": code, "diagnostic_category": category, "diagnostic_outcome": outcome,
                        "trace_id": trace_id, "span_id": span_id, "step": step})
        if job and scope == _INSTRUMENTATION and _PROCESSOR.add_diagnostic(span, job, attrs, publish_now) and exc is not None:
            setattr(exc, "_taozhi_diagnostic_trace", ctx.trace_id)
    except Exception as diagnostic_error:
        _LOG.warning("节点诊断未能记录（%s）", type(diagnostic_error).__name__)


@contextmanager
def node_span(name: str, kind: str, attributes: dict | None = None, *, context=None):
    """在当前或显式父上下文中计时，保留错误类型并传播原异常。"""
    if not _enabled():
        yield None
        return
    token = otel_context.attach(context) if context is not None else None
    try:
        with _workflow_tracer().start_as_current_span(
            name,
            attributes=safe_attributes(attributes),
            openinference_span_kind=OpenInferenceSpanKindValues(kind),
            kind=SpanKind.CLIENT if kind in {"LLM", "TOOL", "EMBEDDING", "RERANKER"} else SpanKind.INTERNAL,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as exc:
                if getattr(exc, "_taozhi_diagnostic_trace", None) != span.get_span_context().trace_id:
                    record_diagnostic(failure_code(kind, name, exc), "failed", exc, publish_now=False)
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(StatusCode.ERROR)
                raise
    finally:
        if token is not None:
            otel_context.detach(token)


@contextmanager
def job_trace(tenant_id: str, job_id: str, run_version: int, name: str):
    """一次 Worker 执行对应一条独立根链路，跨线程时仅传递 OTel Context。"""
    scope = (tenant_id, job_id, run_version)
    root = otel_context.set_value(_JOB, scope, otel_context.Context())
    with node_span(name, "CHAIN", context=root) as span:
        yield span


def current_trace_id() -> str:
    """当前执行的 32 位十六进制 Trace ID，无有效链路时返回空串。"""
    if _enabled():
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return trace.format_trace_id(ctx.trace_id)
    return ""
