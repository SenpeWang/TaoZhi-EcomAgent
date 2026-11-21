"""只接收预定义故障码的 OTel 日志事件，保留发生时间、处理结果和安全元数据。"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from taozhi.domain.errors import Denied
from taozhi.domain.trace_diagnostic import CATALOG, OUTCOMES

EVENT_NAME = "taozhi.diagnostic"
_EXCEPTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,95}")


def http_status_of(exc: BaseException | None) -> int | None:
    """提取 SDK 或 HTTP 客户端明确提供的状态码。"""
    value = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return value if type(value) is int and 100 <= value <= 599 else None


def failure_code(kind: str, name: str, exc: BaseException) -> str:
    """将未在调用点处理的异常归入实际操作所属的故障域。"""
    if isinstance(exc, Denied) and (exc.status in (401, 403) or exc.code in {"SOURCE_CHANGED", "PERMISSION_CHANGED"}):
        return "permission.denied"
    if name.startswith("context."):
        return "context.prepare"
    if type(exc).__name__ == "OutputDeliveryError":
        return "storage.delivery"
    if type(exc).__module__.split(".")[0] in {"psycopg", "psycopg_pool"}:
        return "storage.database"
    return {
        "LLM": "model.invoke_error", "TOOL": "tool.error", "RETRIEVER": "retrieval.search",
        "EMBEDDING": "retrieval.embedding", "RERANKER": "retrieval.rerank",
    }.get(kind, "workflow.step")


def safe_diagnostic(attrs, timestamp: str) -> dict | None:
    """仅从固定故障码生成可见文案，屏蔽数据库或 SDK 中未登记的事件字段。"""
    code, outcome = attrs.get("code"), attrs.get("outcome")
    if not isinstance(code, str) or code not in CATALOG or not isinstance(outcome, str) or outcome not in OUTCOMES:
        return None
    try:
        when = datetime.fromisoformat(timestamp)
        if when.tzinfo is None:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    category, message = CATALOG[code]
    exception = attrs.get("exception_type")
    status = attrs.get("http_status")
    return {
        "timestamp": when.astimezone(timezone.utc).isoformat(),
        "category": category, "code": code, "message": message, "outcome": outcome,
        "level": "error" if outcome == "failed" else "warning",
        "exception_type": exception if isinstance(exception, str) and _EXCEPTION_NAME.fullmatch(exception) else None,
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
    }


def diagnostic_events(events) -> list[dict]:
    """导出本项目的带时间戳 OTel 事件，且不读异常正文和堆栈。"""
    result = []
    for event in events:
        if event.name == EVENT_NAME:
            timestamp = datetime.fromtimestamp(event.timestamp / 1e9, timezone.utc).isoformat()
            diagnostic = safe_diagnostic(event.attributes or {}, timestamp)
            if diagnostic is not None:
                event_id = (event.attributes or {}).get("event_id")
                if isinstance(event_id, str) and re.fullmatch("[0-9a-f]{32}", event_id):
                    diagnostic["event_id"] = event_id
                result.append(diagnostic)
    return result
