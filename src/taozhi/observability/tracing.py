"""OTel 执行轨迹：节点级 span 导出到 Phoenix（OTLP/HTTP）。

设计边界：
- 默认开启（Phoenix 常驻 127.0.0.1:6006），设 TAOZHI_TRACING=0 一键关闭；
- 未安装 opentelemetry 或导出器异常时全部 no-op，业务链路零感知；
- span 属性只放低敏元数据（计数/耗时/job_id），不落问题与答案原文；
- trace_id 回写任务结果，评测报告逐例可与 Phoenix trace 精确关联。
"""
from __future__ import annotations

import os
from contextlib import nullcontext

_READY = None  # None=未探测；True/False=探测结果
_TRACER = None


def _enabled() -> bool:
    global _READY
    if _READY is None:
        _READY = False
        if os.environ.get("TAOZHI_TRACING", "1") not in ("", "0", "false"):
            try:
                import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401
                from opentelemetry import trace  # noqa: F401

                _READY = True
            except Exception:
                _READY = False
    return _READY


def _tracer():
    global _TRACER
    if _TRACER is None:
        from opentelemetry import trace

        _TRACER = trace.get_tracer("taozhi.pipeline")
    return _TRACER


def setup_tracing(service_name: str = "taozhi-worker") -> bool:
    """初始化根 TracerProvider 与 OTLP/HTTP 导出器；失败静默降级为 no-op。"""
    if not _enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.environ.get("TAOZHI_OTLP_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
        # Phoenix 按 resource 属性 openinference.project.name 路由项目
        # （utilities/project.py: get_project_name 只读 resource，不读 span 属性）。
        provider = TracerProvider(resource=Resource.create({
            "service.name": service_name,
            "openinference.project.name": service_name,
        }))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5)))
        trace.set_tracer_provider(provider)
        return True
    except Exception:
        return False


def node_span(name: str, attrs: dict | None = None, context=None):
    """节点级 span 上下文管理器；未启用时返回 nullcontext。"""
    if not _enabled():
        return nullcontext()
    try:
        return _tracer().start_as_current_span(name, context=context, attributes=attrs or {})
    except Exception:
        return nullcontext()


def current_context():
    """取当前 OTel 上下文（供线程池子任务挂父子关系）；未启用返回 None。"""
    if not _enabled():
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def current_trace_id() -> str:
    """当前 span 的 32 位十六进制 trace_id；不可用返回空串。"""
    try:
        if _enabled():
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            if ctx is not None and ctx.is_valid:
                return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""
