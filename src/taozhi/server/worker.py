"""后台异步任务守护进程：队列认领、租约续期与任务分发执行。"""
from __future__ import annotations

import secrets
import signal
import threading
import traceback

from taozhi.application.services.document_service import process_ingestion_job
from taozhi.application.services.task_service import process_question_job, process_research_job
from taozhi.config import load_config
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence import document_repo, job_repo
from taozhi.knowledge import queue
from taozhi.knowledge.db import database_connection
from taozhi.observability.tracing import setup_tracing

STOP = threading.Event()
WORKER = "worker_" + secrets.token_hex(8)


def renew_job_lease(job: dict, done: threading.Event) -> None:
    """周期续约运行中任务租约与 Worker 心跳。"""
    wid = job.get("worker_id") or WORKER
    lease_seconds = load_config().lease_seconds
    while not done.wait(10):
        try:
            with database_connection() as c:
                job_repo.renew_lease_once(c, job["id"], job["run_version"], wid, lease_seconds)
        except Exception:
            pass


def heartbeat() -> None:
    """上报 Worker 存活心跳并清理 1 天前过期节点。"""
    with database_connection() as c:
        job_repo.touch_worker_heartbeat(c, WORKER, prune=True)


def run_job(job: dict) -> None:
    """在独立租约保护下路由执行资料解析、自主调研或问答任务。"""
    done = threading.Event()
    thread = threading.Thread(target=renew_job_lease, args=(job, done), daemon=True)
    thread.start()
    try:
        if job["kind"] == "ingest":
            process_ingestion_job(job)
        elif job["kind"] == "research":
            process_research_job(job)
        else:
            process_question_job(job)
    except Exception as exc:
        message = exc.message if isinstance(exc, Denied) else "任务处理失败，请重试或联系管理员"
        with database_connection() as c:
            row = job_repo.get_job_by_id(c, job["id"], for_update=True)
            if row and row["state"] == "running" and row["run_version"] == job["run_version"]:
                status = "cancelled" if row["cancel_requested"] else "failed"
                job_repo.mark_job_failed_or_cancelled(c, job, status, message)
                if job["kind"] == "ingest":
                    document_repo.mark_document_ingest_failed(
                        c, job["payload"]["document_id"], job["payload"]["version"], message
                    )
        print("任务结束", job["id"], type(exc).__name__, flush=True)
        traceback.print_exc()
    finally:
        done.set()
        thread.join(timeout=2)


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
    """Worker 守护进程主循环入口。"""
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    print("企业任务进程已启动", flush=True)
    print("执行轨迹:", "已启用（OTLP→Phoenix）" if setup_tracing("taozhi-worker") else "未启用", flush=True)
    cfg = load_config()
    if cfg.kafka_enabled:
        queue._worker_id = WORKER
        threading.Thread(target=queue.relay_loop, daemon=True).start()
        threading.Thread(target=queue.consume_forever, args=(run_job, sweep), daemon=True).start()
        idle = 3
    else:
        idle = 1
    while not STOP.is_set():
        try:
            heartbeat()
            if not process_next_job():
                STOP.wait(idle)
        except Exception as exc:
            print("任务进程正在重连", type(exc).__name__, flush=True)
            STOP.wait(3)


if __name__ == "__main__":
    main()
