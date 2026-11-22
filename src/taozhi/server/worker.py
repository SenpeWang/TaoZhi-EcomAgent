"""后台异步任务守护进程：队列认领、租约续期、优雅停机与任务分发执行。"""
from __future__ import annotations

import logging
import secrets
import signal
import threading
from contextlib import nullcontext

from opentelemetry import context as otel_context

from taozhi.application.documents.ingestion import process_ingestion_job
from taozhi.application.rag.execution import process_question_job
from taozhi.application.research.execution import process_research_job
from taozhi.config import load_config
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence import document_repo, job_repo
from taozhi.infrastructure import task_queue
from taozhi.infrastructure.persistence.database import close_pools, database_connection
from taozhi.observability.span_writer import flush_spans
from taozhi.observability.tracing import job_trace, record_diagnostic, setup_tracing

_LOG = logging.getLogger(__name__)
STOP = threading.Event()
WORKER = "worker_" + secrets.token_hex(8)


def renew_job_lease(job: dict, done: threading.Event, parent_context) -> None:
    """周期续约运行中任务租约与 Worker 心跳。

    Args:
        job: 正在执行的任务记录字典。
        done: 任务结束通知事件，置位后退出续约循环。
        parent_context: 当前执行的 OTel 上下文。
    """
    wid = job.get("worker_id") or WORKER
    lease_seconds = load_config().lease_seconds
    while not done.wait(10):
        try:
            with database_connection() as c:
                job_repo.renew_lease_once(c, job["id"], job["run_version"], wid, lease_seconds)
        except Exception as exc:
            token = otel_context.attach(parent_context)
            try:
                record_diagnostic("workflow.lease", "retry", exc)
            finally:
                otel_context.detach(token)


def heartbeat() -> None:
    """上报 Worker 存活心跳并清理 1 天前过期节点。"""
    with database_connection() as c:
        job_repo.touch_worker_heartbeat(c, WORKER, prune=True)


def run_job(job: dict) -> None:
    """在独立租约保护下路由执行资料解析、自主调研或问答任务。"""
    done = threading.Event()
    scope = nullcontext() if job["kind"] == "ingest" else job_trace(
        job["tenant_id"], job["id"], job["run_version"], f"task.{job['kind']}",
    )
    try:
        with scope:
            thread = threading.Thread(target=renew_job_lease, args=(job, done, otel_context.get_current()), daemon=True)
            thread.start()
            try:
                if job["kind"] == "ingest":
                    process_ingestion_job(job)
                elif job["kind"] == "research":
                    process_research_job(job)
                else:
                    process_question_job(job)
            finally:
                done.set()
                thread.join(timeout=2)
    except Exception as exc:
        message = exc.message if isinstance(exc, Denied) else f"任务处理失败（{type(exc).__name__}），请重试或联系管理员"
        with database_connection() as c:
            row = job_repo.get_job_by_id(c, job["id"], for_update=True)
            if row and row["state"] == "running" and row["run_version"] == job["run_version"]:
                status = "cancelled" if row["cancel_requested"] else "failed"
                job_repo.mark_job_failed_or_cancelled(c, job, status, message)
                if job["kind"] == "ingest":
                    document_repo.mark_document_ingest_failed(
                        c, job["payload"]["document_id"], job["payload"]["version"], message
                    )
        _LOG.error("任务执行失败（任务=%s，异常类型=%s）", job["id"], type(exc).__name__)


def process_next_job() -> bool:
    """尝试认领并执行队列中的下一条待处理任务。"""
    job = job_repo.claim_next_job(WORKER)
    if not job:
        return False
    run_job(job)
    return True


def sweep() -> None:
    """Kafka 消费空窗对账回调：续报心跳、超时复核清理并补认领遗留任务。"""
    heartbeat()
    job_repo.expire_stale_reviews()
    process_next_job()


def main() -> None:
    """Worker 守护进程主循环入口，支持单一信号量下的优雅停机与线程回收。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    setup_tracing()
    _LOG.info("企业任务进程已启动")
    cfg = load_config()
    bg_threads: list[threading.Thread] = []
    if cfg.kafka_enabled:
        relay_t = threading.Thread(target=task_queue.relay_loop, kwargs={"stop_event": STOP}, daemon=True)
        consume_t = threading.Thread(
            target=task_queue.consume_forever,
            args=(run_job, sweep),
            kwargs={"worker_id": WORKER, "stop_event": STOP},
            daemon=True,
        )
        relay_t.start()
        consume_t.start()
        bg_threads.extend([relay_t, consume_t])
        idle = 3
    else:
        idle = 1

    try:
        while not STOP.is_set():
            try:
                heartbeat()
                if not process_next_job():
                    STOP.wait(idle)
            except Exception as exc:
                _LOG.warning("任务进程正在重连（%s）", type(exc).__name__)
                STOP.wait(3)
    finally:
        STOP.set()
        for t in bg_threads:
            t.join(timeout=5)
        flush_spans()
        close_pools()


if __name__ == "__main__":
    main()
