"""运行企业问答任务，调度知识问答或经营指标工作流并校验结果发布权限。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from taozhi.application.bi.intent import detect_data_intent, may_request_metric
from taozhi.application.bi.workflow import build_bi_result, build_bi_workflow
from taozhi.application.common.task_guard import empty_token_usage, validate_task_execution
from taozhi.domain.errors import Denied
from taozhi.domain.question_context import recalls_previous_evidence, refers_to_previous_question, requested_business_resources
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.access_repo import get_tenant_epoch, load_principal, sources_valid
from taozhi.infrastructure.persistence.checkpoints import postgres_checkpointer
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.infrastructure.knowledge.semantic_cache import get_semantic_cache
from taozhi.observability.tracing import current_trace_id, node_span, record_diagnostic


def _elapsed_ms(start_dt: datetime | None, end_dt: datetime | None = None) -> int:
    """计算两个时间戳之间的毫秒差，兼容时区缺失场景。"""
    if start_dt is None:
        return 0
    if end_dt is None:
        end_dt = datetime.now(timezone.utc) if start_dt.tzinfo else datetime.now()
    elif bool(start_dt.tzinfo) != bool(end_dt.tzinfo):
        end_dt = end_dt.replace(tzinfo=start_dt.tzinfo)
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _try_answer_from_semantic_cache(job: dict[str, Any], cache: Any, queue_wait_ms: int) -> bool:
    """检索语义缓存并在来源权限实时有效时直接回填问答终态（同步租户跨进程代际水位）。"""
    with node_span("knowledge.cache_lookup", "CHAIN"), database_connection() as c:
        p = load_principal(c, job["owner_id"])
        epoch = get_tenant_epoch(c, job["tenant_id"])
        cached = None
        for _ in range(5):
            cand = cache.get(
                job["tenant_id"],
                job["question"],
                input_level=job["input_level"],
                boss=p.boss,
                epoch=epoch,
            )
            if not cand:
                break
            if cand.get("sources") and sources_valid(c, p, cand.get("sources", [])):
                cached = cand
                break
            if cand.get("entry_id"):
                cache.invalidate(job["tenant_id"], entry_id=cand["entry_id"])
    if not cached:
        return False

    with node_span("worker.semantic_cache_hit", "CHAIN"), database_connection() as c:
        row = job_repo.lock_running_job(c, job["id"], job["run_version"])
        p = load_principal(c, row["owner_id"])
        if p.version != row["owner_version"]:
            raise Denied("人员权限已变化，请重新提交", "PERMISSION_CHANGED", 409)
        if get_tenant_epoch(c, p.tenant_id) != epoch or not sources_valid(c, p, cached["sources"]):
            return False
        e2e_ms = _elapsed_ms(job.get("created_at"))
        result = dict(
            answer=cached["answer"],
            answer_basis="internal_evidence",
            public_sources=[],
            guidance="",
            citations=cached["citations"],
            specialists=[{"name": "semantic_cache", "count": 1}],
            missing=[],
            hard_block=False,
            external_blocked=False,
            model_calls=0,
            usage=[{"role": "semantic_cache", "elapsed_ms": cached["latency_ms"], "outcome": "hit"}],
            cache_hit=True,
            similarity=cached["similarity"],
            timing={"queue_wait_ms": queue_wait_ms, "cache_lookup_ms": cached["latency_ms"], "e2e_ms": e2e_ms},
            token_usage=empty_token_usage(),
            degraded={},
        )
        trace_id = current_trace_id()
        if trace_id:
            result["trace_id"] = trace_id
        job_repo.complete_job(
            c,
            tenant_id=p.tenant_id,
            job_id=job["id"],
            run_version=job["run_version"],
            state="review" if row["payload"].get("require_review") else "completed",
            stage="等待业务负责人审核" if row["payload"].get("require_review") else "已完成（命中语义缓存）",
            result=result,
            sources=cached["sources"],
            input_level=job["input_level"],
        )
        return True


def _build_question_result(state: dict[str, Any]) -> dict[str, Any]:
    """将 RAG 问答工作流终态裁剪组装为前端与报告可消费的结果负载。"""
    citation_fields = ("document_id", "version", "title", "quote", "page", "chunk_id")
    finding_counts = Counter(finding["role"] for finding in state["findings"])
    used = {finding["evidence_index"] for finding in state["findings"]}
    used.update(state.get("excerpt_indices", ()))
    return dict(
        answer=state["answer"],
        answer_basis=state["answer_basis"],
        public_sources=state["public_sources"],
        guidance=state["guidance"],
        citations=[{**{field: item[field] for field in citation_fields}, "cited": index in used}
                   for index, item in enumerate(state["evidence"])],
        specialists=[{"name": role, "count": finding_counts[role]} for role in state["selected_agents"]],
        missing=state["missing"],
        hard_block=state["hard_block"],
        external_blocked=state["external_blocked"],
        model_calls=state["model_calls"],
        usage=state["usage"],
        cache_hit=False,
        token_usage=state.get("token_usage") or empty_token_usage(),
        degraded=state.get("degraded") or {},
    )


def process_question_job(job: dict[str, Any]) -> None:
    """按意图分发至独立工作流：ChatBI 受控问数（application/bi）或知识库 RAG（application/rag）。"""
    question = job["question"]
    payload = job["payload"]
    business = requested_business_resources(question, payload)
    cache_allowed = payload.get("allow_external", True) and not (
        business
        or recalls_previous_evidence(question, job.get("conversation_id") or "")
        or refers_to_previous_question(question)
    )
    data_intent: dict[str, Any] | None = None
    if not business and may_request_metric(question):
        try:
            with database_connection() as c:
                p_init = load_principal(c, job["owner_id"])
                data_intent = detect_data_intent(c, p_init, question)
        except Exception as exc:
            cache_allowed = False
            record_diagnostic("workflow.intent", "fallback", exc)

    is_bi = bool(data_intent)
    cache = get_semantic_cache() if cache_allowed and not is_bi else None
    queue_wait_ms = _elapsed_ms(job.get("created_at"), job.get("started_at"))
    if cache is not None and _try_answer_from_semantic_cache(job, cache, queue_wait_ms):
        return

    thread_id = f"{job['id']}:{job['run_version']}"
    initial_state = {"job_id": job["id"], "run_version": job["run_version"]}
    with postgres_checkpointer() as saver:
        if is_bi:
            flow = build_bi_workflow(saver)
            initial_state["data_intent"] = data_intent
        else:
            from taozhi.application.rag.workflow import build_rag_workflow

            flow = build_rag_workflow(saver)
        with node_span("worker.bi" if is_bi else "worker.question", "CHAIN"):
            state = flow.invoke(initial_state, {"configurable": {"thread_id": thread_id}})
            trace_id = current_trace_id()
    validate_task_execution(state, "最终权限核验", 98)
    with database_connection() as c:
        row = job_repo.lock_running_job(c, job["id"], job["run_version"])
        p = load_principal(c, row["owner_id"])
        sources = state["sources"]
        if p.version != row["owner_version"] or not sources_valid(c, p, sources):
            raise Denied("人员或来源权限已变化", "SOURCE_CHANGED", 409)
        if is_bi:
            result = build_bi_result(state)
            requires_review = row["payload"].get("require_review")
        else:
            result = _build_question_result(state)
            requires_review = state["review"]
        result["timing"] = {
            **(state.get("timing") or {}),
            "queue_wait_ms": queue_wait_ms,
            "e2e_ms": _elapsed_ms(row.get("created_at") or job.get("created_at")),
        }
        if trace_id:
            result["trace_id"] = trace_id
        status = "review" if requires_review else "completed"
        job_repo.complete_job(
            c,
            tenant_id=p.tenant_id,
            job_id=job["id"],
            run_version=job["run_version"],
            state=status,
            stage="等待业务负责人审核" if requires_review else "已完成",
            result=result,
            sources=sources,
            input_level=state["input_level"],
        )
        if cache is not None:
            epoch = get_tenant_epoch(c, p.tenant_id)
        if status == "completed":
            job_repo.delete_checkpoint_threads(c, [thread_id])

    if cache is not None and status == "completed" and state["findings"] and not state["missing"]:
        try:
            cache.put(
                job["tenant_id"],
                job["question"],
                state["answer"],
                result["citations"],
                sources,
                state.get("input_level", job["input_level"]),
                redacted=not p.boss,
                epoch=epoch,
            )
        except Exception as exc:
            record_diagnostic("storage.write", "skipped", exc)
