"""批量持久化任务执行中或已结束的 OpenTelemetry Span。"""
from __future__ import annotations

from taozhi.knowledge.db import as_jsonb, database_connection

_UPSERT = """
    INSERT INTO task_spans
        (tenant_id, job_id, run_version, trace_id, span_id, parent_span_id, name, kind,
         status, started_at, ended_at, duration_ms, error_type, attributes)
    SELECT j.tenant_id, j.id, %(run_version)s, %(trace_id)s, %(span_id)s, %(parent_span_id)s,
           %(name)s, %(kind)s, %(status)s, %(started_at)s, %(ended_at)s, %(duration_ms)s,
           %(error_type)s, %(attributes)s
    FROM jobs j WHERE j.tenant_id = %(tenant_id)s AND j.id = %(job_id)s AND j.kind IN ('ask','research')
    ON CONFLICT (trace_id, span_id) DO UPDATE
        SET kind=EXCLUDED.kind, status=EXCLUDED.status, ended_at=EXCLUDED.ended_at, duration_ms=EXCLUDED.duration_ms,
            error_type=EXCLUDED.error_type, attributes=EXCLUDED.attributes
    WHERE EXCLUDED.ended_at IS NOT NULL AND task_spans.ended_at IS NULL
      AND (task_spans.tenant_id, task_spans.job_id, task_spans.run_version)
        = (EXCLUDED.tenant_id, EXCLUDED.job_id, EXCLUDED.run_version)
"""


def write_spans(batch: list[dict]) -> None:
    """独立写线程按接收顺序批量提交，重试保持幂等，任务已删除时跳过。"""
    rows = [{**span, "attributes": as_jsonb(span["attributes"])} for span in batch]
    with database_connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '1s'")
        conn.execute("SET LOCAL statement_timeout = '3s'")
        with conn.cursor() as cursor:
            cursor.executemany(_UPSERT, rows)
