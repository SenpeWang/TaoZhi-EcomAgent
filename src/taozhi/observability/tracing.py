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

from openinference.instrumentation import OITracer, TraceConfig
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, StatusCode

from .span_attributes import safe_attributes
from .span_writer import flush_spans, submit_span

_LOG = logging.getLogger(__name__)
_JOB = otel_context.create_key("taozhi.job")
_SPAN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,119}")
_INSTRUMENTATION = "taozhi.pipeline"
_PROVIDER_LOCK = threading.Lock()
_provider: TracerProvider | None = None


def _enabled() -> bool:
    return os.environ.get("TAOZHI_TRACING", "1").strip().lower() not in {"", "0", "false"}


class _TaskSpanProcessor(SpanProcessor):
    """投递当前任务的运行中及结束快照，独立评测不写入租户数据库。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = OrderedDict()

    def on_start(self, span, parent_context=None):
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
            # 第三方库可能永不结束 Span，限制进程内关联表的最大规模。
            if len(self._active) >= 16384:
                self._active.popitem(last=False)
            self._active[key] = job
        self._snapshot(span, job)

    def on_end(self, span: ReadableSpan):
        with self._lock:
            job = self._active.pop((span.context.trace_id, span.context.span_id), None)
        if job is not None:
            self._snapshot(span, job)

    def _snapshot(self, span: ReadableSpan, job: tuple) -> None:
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
                # 第三方操作名可能内含完整 SQL、URL、请求文本或上游报错。
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
            })
        except Exception as exc:
            # 采集故障不能改变原始业务调用的返回值或异常。
            _LOG.warning("执行链路采集失败 (%s)", type(exc).__name__)


def _local_provider(service_name: str = "taozhi-worker") -> TracerProvider:
    global _provider
    if _provider is None:
        with _PROVIDER_LOCK:
            if _provider is None:
                provider = TracerProvider(
                    resource=Resource({"service.name": service_name}),
                    sampler=ALWAYS_ON,
                    shutdown_on_exit=False,
                )
                provider.add_span_processor(_TaskSpanProcessor())
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
