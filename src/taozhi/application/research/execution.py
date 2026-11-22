"""执行市场调研任务并将进度、正文和中途干预关联到当前执行版本。"""
from __future__ import annotations

from typing import Any

from taozhi.application.research.graph import execute_autonomous_agents
from taozhi.application.services import task_output
from taozhi.config import get_settings
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.access_repo import load_principal
from taozhi.infrastructure.persistence.checkpoints import postgres_checkpointer
from taozhi.infrastructure.persistence.database import database_connection


def process_research_job(job: dict[str, Any]) -> None:
    """运行多智能体市场调研主副协作图并回传终态报告。"""

    def emit(stage: str, progress: int) -> None:
        with database_connection() as c:
            job_repo.update_job_stage(c, job["tenant_id"], job["id"], job["run_version"], stage, progress)

    def record_model_call(role: str, event: dict) -> None:
        with database_connection() as c:
            job_repo.record_research_model_call(c, job, role, event)

    def check_cancel() -> bool:
        with database_connection() as c:
            r = job_repo.get_job_by_id(c, job["id"])
            return not r or r["state"] != "running" or r["run_version"] != job["run_version"] or bool(r["cancel_requested"])

    def fetch_steering() -> list:
        with database_connection() as c:
            r = job_repo.get_job_by_id(c, job["id"])
            return list(((r or {}).get("payload") or {}).get("steering_inputs") or [])

    settings = get_settings()
    payload = job["payload"]
    keyword = payload.get("keyword") or job["question"]

    with postgres_checkpointer() as saver:
        result = execute_autonomous_agents(
            keyword=keyword,
            user_query=job["question"],
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            run_version=job["run_version"],
            conversation_id=job["conversation_id"],
            checkpointer=saver,
            require_checkpointer=True,
            emit_event_fn=emit,
            result_delta_fn=lambda text: task_output.publish_report_delta(job["id"], job["run_version"], text),
            result_reset_fn=lambda: task_output.publish_report_delta(job["id"], job["run_version"], None),
            model_call_fn=record_model_call,
            initial_steering=payload.get("steering_inputs", []),
            timeout_seconds=max(240.0, float(settings.llm_timeout or 90.0) * 2.5),
            max_turns=int(settings.research_max_turns),
            max_parallel_subagents=int(settings.research_max_parallel_subagents),
            max_subagent_steps=int(settings.research_max_subagent_steps),
            check_cancel_fn=check_cancel,
            fetch_steering_fn=fetch_steering,
        )

    if result.get("cancelled"):
        raise Denied("任务已被用户取消", "CANCELLED", 409)

    stage_title = "调研报告已生成"
    with database_connection() as c:
        row = job_repo.lock_running_job(c, job["id"], job["run_version"])
        owner = load_principal(c, row["owner_id"])
        if owner.version != row["owner_version"]:
            raise Denied("人员权限已变化，请重新提交", "PERMISSION_CHANGED", 409)
        job_repo.complete_job(
            c,
            tenant_id=job["tenant_id"],
            job_id=job["id"],
            run_version=job["run_version"],
            state="review" if row["payload"].get("require_review") else "completed",
            stage="等待业务负责人审核" if row["payload"].get("require_review") else stage_title,
            result=result,
            sources=[],
        )
