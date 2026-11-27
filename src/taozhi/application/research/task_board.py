"""调研任务板应用服务：把执行态写入持久任务板。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from typing import Any, Iterator, Sequence

from taozhi.domain.research import SubagentTaskSpec
from taozhi.infrastructure.persistence import research_task_repo
from taozhi.infrastructure.persistence.database import database_connection

_CURRENT: ContextVar["TaskBoard | None"] = ContextVar("research_task_board", default=None)


def _brief(spec: SubagentTaskSpec) -> dict[str, Any]:
    """把领域任务规约转换为任务板 JSON。"""
    payload = asdict(spec)
    return {
        "task_id": payload["task_id"],
        "role": payload["role"],
        "goal": payload["goal"],
        "suggested_tools": list(payload.get("suggested_tools") or []),
        "seed_queries": list(payload.get("seed_queries") or []),
        "channel_hint": payload.get("channel_hint") or "auto",
        "output_format": payload.get("output_format"),
        "source_guidance": payload.get("source_guidance"),
        "boundaries": payload.get("boundaries"),
        "expected_evidence": payload.get("expected_evidence"),
        "model_tier": payload.get("model_tier") or "fast",
        "write_scopes": [],
    }


class TaskBoard:
    """绑定一次调研执行的持久任务板。"""

    def __init__(self, *, tenant_id: str, job_id: str, run_version: int) -> None:
        self.tenant_id = tenant_id
        self.job_id = job_id
        self.run_version = int(run_version)

    def create_tasks(self, specs: Sequence[SubagentTaskSpec]) -> list[dict[str, Any]]:
        """创建本次派工的任务行。"""
        with database_connection() as conn:
            return research_task_repo.create_tasks(
                conn,
                tenant_id=self.tenant_id,
                job_id=self.job_id,
                run_version=self.run_version,
                briefs=[_brief(spec) for spec in specs],
            )

    def start(self, task_key: str, owner_agent: str) -> dict[str, Any] | None:
        """认领任务并置为 running；任务不存在或已开始返回 None。"""
        with database_connection() as conn:
            row = research_task_repo.find_task(
                conn, job_id=self.job_id, run_version=self.run_version, task_key=task_key
            )
            if row is None:
                return None
            return research_task_repo.claim_task(conn, row["id"], owner_agent)

    def complete(self, task_key: str, result: dict[str, Any]) -> dict[str, Any] | None:
        """把任务置为 done 并写入结果。"""
        return self._finish(task_key, "done", result=result)

    def fail(self, task_key: str, error: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """把任务置为 failed。"""
        return self._finish(task_key, "failed", result=result, error=error)

    def block(self, task_key: str, error: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """把任务置为 blocked（缺输入/依赖）。"""
        return self._finish(task_key, "blocked", result=result, error=error)

    def _finish(
        self,
        task_key: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any] | None:
        """查找任务行并按指定终态收尾。

        Args:
            task_key: 任务键。
            status: 终态（done/failed/blocked）。
            result: 任务结果。
            error: 失败原因。

        Returns:
            更新后的任务行；任务不存在时返回 None。
        """
        with database_connection() as conn:
            row = research_task_repo.find_task(
                conn, job_id=self.job_id, run_version=self.run_version, task_key=task_key
            )
            if row is None:
                return None
            return research_task_repo.finish_task(
                conn, row["id"], status=status, result=result, error=error
            )

    def create_adhoc(
        self,
        *,
        task_key: str,
        role: str,
        brief: dict[str, Any],
        owner_agent: str,
    ) -> dict[str, Any] | None:
        """为速答/协调员等临时动作创建并立即认领一条任务。"""
        payload = {"task_id": task_key, "role": role, **brief, "write_scopes": []}
        with database_connection() as conn:
            rows = research_task_repo.create_tasks(
                conn,
                tenant_id=self.tenant_id,
                job_id=self.job_id,
                run_version=self.run_version,
                briefs=[payload],
            )
        row = rows[0] if rows else None
        if row is None:
            return None
        return self.start(task_key, owner_agent)

    def cancel_incomplete(self) -> int:
        """取消本次执行仍未结束的任务。"""
        with database_connection() as conn:
            return research_task_repo.cancel_incomplete(
                conn, job_id=self.job_id, run_version=self.run_version
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出本次执行的任务板。"""
        with database_connection() as conn:
            return research_task_repo.list_tasks(
                conn, tenant_id=self.tenant_id, job_id=self.job_id, run_version=self.run_version
            )

    def comment(self, task_id: str, author_agent: str, body: str) -> dict[str, Any]:
        """追加任务评论。"""
        with database_connection() as conn:
            return research_task_repo.add_comment(conn, task_id=task_id, author_agent=author_agent, body=body)

    def comments(self, task_id: str) -> list[dict[str, Any]]:
        """读取任务评论。"""
        with database_connection() as conn:
            return research_task_repo.list_comments(conn, task_id)


def current_task_board() -> TaskBoard | None:
    """返回当前执行绑定的任务板；未绑定时为 None。"""
    return _CURRENT.get()


@contextmanager
def use_task_board(board: TaskBoard | None) -> Iterator[TaskBoard | None]:
    """在一次调研执行内绑定任务板。"""
    token = _CURRENT.set(board)
    try:
        yield board
    finally:
        _CURRENT.reset(token)


__all__ = ["TaskBoard", "current_task_board", "use_task_board"]
