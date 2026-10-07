"""全链路追踪（PRD N3）：LangSmith Trace + JSONL 本地落盘。"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..config import get_settings


def enable_langsmith() -> bool:
    settings = get_settings()
    if not settings.langsmith_enabled or not settings.langsmith_api_key:
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return True


class TraceSpan:
    def __init__(self, name: str, parent: "TaskTracer", **fields) -> None:
        self.name = name
        self.parent = parent
        self.fields: Dict[str, Any] = dict(fields)
        self.started = time.perf_counter()
        self.status = "ok"
        self.output: Any = None

    def set_output(self, output: Any) -> None:
        self.output = output

    def fail(self, error: str) -> None:
        self.status = "error"
        self.fields["error"] = error

    def close(self) -> Dict[str, Any]:
        elapsed = time.perf_counter() - self.started
        record = {
            "span": self.name,
            "trace_id": self.parent.trace_id,
            "elapsed_s": round(elapsed, 4),
            "status": self.status,
            **self.fields,
        }
        self.parent._write(record)
        return record


class TaskTracer:
    """一次研究任务的 tracer：span 落盘为 JSONL，便于审计与回放。"""

    def __init__(self, trace_id: Optional[str] = None, name: str = "research") -> None:
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.name = name
        settings = get_settings()
        self.path: Path = settings.trace_dir / f"{name}-{self.trace_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spans: List[Dict[str, Any]] = []

    @contextmanager
    def span(self, name: str, **fields) -> Iterator[TraceSpan]:
        handle = TraceSpan(name, self, **fields)
        try:
            yield handle
        except Exception as exc:  # noqa: BLE001
            handle.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.spans.append(handle.close())

    def _write(self, record: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def summary(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": len(self.spans),
            "total_s": round(sum(s.get("elapsed_s", 0) for s in self.spans), 3),
            "path": str(self.path),
        }


def write_audit(payload: Dict[str, Any]) -> None:
    """会话审计落盘（JSONL）。"""
    settings = get_settings()
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    path = settings.audit_dir / "audit.jsonl"
    payload = {"ts": time.time(), **payload}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
