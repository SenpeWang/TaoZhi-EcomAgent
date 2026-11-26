"""调研任务板仓储：任务、依赖、评论与结果。"""
from __future__ import annotations

from typing import Any, Sequence

from taozhi.infrastructure.persistence.database import as_jsonb, fetch_all, fetch_one
from taozhi.infrastructure.persistence.job_repo import generate_id

TASK_STATUSES = ("ready", "running", "done", "failed", "blocked", "cancelled")


def create_tasks(
    conn: Any,
    *,
    tenant_id: str,
    job_id: str,
    run_version: int,
    briefs: Sequence[dict[str, Any]],
    parent_task_id: str | None = None,
) -> list[dict[str, Any]]:
    """幂等创建任务行；同一 task_key 只更新简报，不重置状态。"""
    created: list[dict[str, Any]] = []
    for brief in briefs:
        task_key = str(brief.get("task_id") or "").strip()
        if not task_key:
            raise ValueError("调研任务缺少 task_id")
        row = fetch_one(
            conn,
            """
            INSERT INTO research_tasks
                (id, tenant_id, job_id, run_version, parent_task_id, task_key, role,
                 status, brief, write_scopes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready', %s, %s)
            ON CONFLICT (job_id, run_version, task_key)
            DO UPDATE SET brief = EXCLUDED.brief
            RETURNING *
            """,
            (
                generate_id("rt"),
                tenant_id,
                job_id,
                run_version,
                parent_task_id,
                task_key,
                str(brief.get("role") or "research.generalist"),
                as_jsonb(brief),
                as_jsonb(list(brief.get("write_scopes") or [])),
            ),
        )
        for dependency in brief.get("depends_on") or []:
            conn.execute(
                """
                INSERT INTO research_task_deps (task_id, depends_on_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (row["id"], dependency),
            )
        created.append(row)
    return created


def get_task(conn: Any, task_id: str) -> dict[str, Any] | None:
    """按任务行 ID 读取。"""
    return fetch_one(conn, "SELECT * FROM research_tasks WHERE id = %s", (task_id,))


def find_task(conn: Any, *, job_id: str, run_version: int, task_key: str) -> dict[str, Any] | None:
    """按业务 task_key 查找任务行。"""
    return fetch_one(
        conn,
        "SELECT * FROM research_tasks WHERE job_id=%s AND run_version=%s AND task_key=%s",
        (job_id, run_version, task_key),
    )


def list_tasks(conn: Any, *, tenant_id: str, job_id: str, run_version: int) -> list[dict[str, Any]]:
    """列出某次调研执行的全部任务。"""
    return fetch_all(
        conn,
        "SELECT * FROM research_tasks WHERE tenant_id=%s AND job_id=%s AND run_version=%s "
        "ORDER BY created_at, id",
        (tenant_id, job_id, run_version),
    )


def claim_task(conn: Any, task_id: str, owner_agent: str) -> dict[str, Any] | None:
    """原子认领 ready 任务并进入 running。"""
    return fetch_one(
        conn,
        """
        UPDATE research_tasks
        SET status='running', owner_agent=%s, revision=revision + 1, started_at=now()
        WHERE id=%s AND status='ready'
        RETURNING *
        """,
        (owner_agent, task_id),
    )


def finish_task(
    conn: Any,
    task_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any] | None:
    """结束 running 任务；返回 None 表示状态或 revision 已失效。"""
    if status not in ("done", "failed", "blocked", "cancelled"):
        raise ValueError(f"非法任务终态：{status}")
    return fetch_one(
        conn,
        """
        UPDATE research_tasks
        SET status=%s, result=%s, error=%s, revision=revision + 1, finished_at=now()
        WHERE id=%s AND status='running'
        RETURNING *
        """,
        (status, as_jsonb(result) if result is not None else None, error, task_id),
    )


def cancel_incomplete(conn: Any, *, job_id: str, run_version: int) -> int:
    """取消某次执行中仍未结束的任务，返回受影响行数。"""
    cursor = conn.execute(
        """
        UPDATE research_tasks
        SET status='cancelled', revision=revision + 1, finished_at=now()
        WHERE job_id=%s AND run_version=%s AND status IN ('ready','running')
        """,
        (job_id, run_version),
    )
    return int(cursor.rowcount or 0)


def add_comment(conn: Any, *, task_id: str, author_agent: str, body: str) -> dict[str, Any]:
    """追加一条任务评论。"""
    row = fetch_one(
        conn,
        """
        INSERT INTO research_task_comments (task_id, author_agent, body)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (task_id, author_agent, body),
    )
    if row is None:
        raise RuntimeError("任务评论写入失败")
    return row


def list_comments(conn: Any, task_id: str) -> list[dict[str, Any]]:
    """按时间列出任务评论。"""
    return fetch_all(
        conn,
        "SELECT * FROM research_task_comments WHERE task_id=%s ORDER BY created_at, id",
        (task_id,),
    )