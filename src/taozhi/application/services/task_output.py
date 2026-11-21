"""任务正文的受权增量发布、执行版本隔离与重连读取。"""
from __future__ import annotations

from dataclasses import dataclass

from taozhi.domain.access import can_task
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.access_repo import load_principal, sources_valid
from taozhi.knowledge.db import database_connection

_PAGE_SIZE = 48
_TERMINAL_STATES = {"completed", "review", "cancelled", "failed", "rejected", "legacy"}


def _publish(job_id: str, run_version: int, text: str | None, sources: list[dict]) -> None:
    """预验来源后再锁定当前执行写入，业务系统的实时核验不占用任务行锁。"""
    if text == "":
        return
    with database_connection() as conn:
        if sources:
            job = job_repo.get_job_by_id(conn, job_id)
            if not job or job["run_version"] != run_version or job["payload"].get("require_review"):
                return
            try:
                owner = load_principal(conn, job["owner_id"])
            except Denied:
                return
            if owner.version != job["owner_version"] or not sources_valid(conn, owner, sources):
                return
        job_repo.write_running_output(conn, job_id, run_version, text, sources)


def publish_answer_delta(job_id: str, run_version: int, text: str, sources: list[dict]) -> None:
    """发布通过问答质量核验的正文片段及该回答依赖的完整来源集合。"""
    _publish(job_id, run_version, text, sources)


def publish_report_delta(job_id: str, run_version: int, text: str | None) -> None:
    """发布市场调研报告的实际文本块；模型回退用空值显式撤回上一份草稿。"""
    _publish(job_id, run_version, text, [])


@dataclass
class StreamPage:
    """一批已按读者权限过滤的事件和下一轮续传所需的数据库水位。"""

    run_version: int
    cursor: int
    events: list[tuple[str, dict]]
    output_allowed: bool
    has_more: bool
    terminal: dict | None


def read_stream_page(p: Principal, task_id: str, cursor: int, *, draft_visible: bool, check_idle: bool = True) -> StreamPage:
    """在每次数据交付前复核当前执行、发布条件、来源和所有者权限代际。"""
    with database_connection() as conn:
        job = job_repo.get_stream_page(conn, p.tenant_id, task_id, cursor, _PAGE_SIZE + 1)
        if not job or not can_task(p, job):
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        rows = job["events"] or []
        has_more = len(rows) > _PAGE_SIZE
        rows = rows[:_PAGE_SIZE]
        publishable_state = job["state"] == "completed" or job["state"] == "running" and job["lease_valid"]
        allowed = bool(publishable_state and not job["cancel_requested"] and not job["require_review"])
        verify = bool(rows) or job["state"] in _TERMINAL_STATES or draft_visible and check_idle
        source_ok = not verify or not job["sources"] or sources_valid(conn, p, job["sources"])
        allowed = allowed and source_ok
        if verify and allowed and (draft_visible or any(row["event_type"] == "result_delta" for row in rows)):
            try:
                owner = p if job["owner_id"] == p.id else load_principal(conn, job["owner_id"])
                allowed = owner.version == job["owner_version"] and (owner.id == p.id or sources_valid(conn, owner, job["sources"]))
            except Denied:
                allowed = False
        events = []
        for row in rows:
            kind = row["event_type"]
            record = {"id": row["id"], "run_version": row["run_version"]}
            if kind == "stage":
                stage = row["stage"] if source_ok else "任务状态已更新"
                record.update(stage=stage if p.id == job["owner_id"] or p.boss else redact(stage),
                              progress=row["progress"], status=row["status"])
            elif kind == "result_delta":
                if not allowed:
                    continue
                record["text"] = row["output_text"]
            events.append((kind, record))
        terminal = None
        if not has_more and job["state"] in _TERMINAL_STATES:
            terminal = {"id": job["id"], "run_version": job["run_version"], "state": job["state"], "progress": job["progress"],
                        "stage": job["stage"] if source_ok else "任务状态已更新", "error": job["error"] if source_ok else ""}
            if p.id != job["owner_id"] and not p.boss:
                terminal["stage"], terminal["error"] = redact(terminal["stage"]), redact(terminal["error"])
        return StreamPage(job["run_version"], rows[-1]["id"] if rows else cursor, events, allowed, has_more, terminal)
