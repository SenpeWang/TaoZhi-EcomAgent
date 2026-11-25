"""知识问答的首轮检索、单次补查及发布前证据分流。"""
from __future__ import annotations

from taozhi.application.common.task_guard import merge_token_usage, validate_task_execution
from taozhi.config import load_config
from taozhi.core.memory.service import get_followup_context
from taozhi.domain.entities import Principal
from taozhi.domain.question_context import (
    extract_search_keyword,
    refers_to_previous_question,
    requested_business_resources,
    requests_record_overview,
)
from taozhi.domain.ranking import fuse_evidence_rounds
from taozhi.domain.rag_verification import RetrievalDecision, fact_subjects, is_finance_evidence
from taozhi.domain.text_guard import detect_injection
from taozhi.infrastructure.persistence.access_repo import sources_valid
from taozhi.infrastructure.persistence.job_repo import conversation_question_reference
from taozhi.infrastructure.business_gateway import lookup
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.infrastructure.knowledge.retriever import search_documents
from taozhi.observability.tracing import node_span
from taozhi.orchestration.controller import DeterministicController
from .phases import RagPhase
from .query_expansion import build_hyde_vector
from .state import State


_EVIDENCE_LIMIT = 10
_OVERVIEW_FIELDS = {"stock": ("available", "price_minor"), "orders": ("status",)}


def _query_context(conn, p: Principal, job: dict, state: State, retry: bool) -> tuple[str, str | None, bool]:
    """不将前次问题写入检查点，按其原始外发授权决定是否允许 HyDE。"""
    question = state["question"]
    reference_id = state.get("prior_question_id")
    if not refers_to_previous_question(question) or (retry and reference_id is None):
        return question, None, True
    previous = conversation_question_reference(
        conn, p.tenant_id, p.id, job.get("conversation_id") or "", (job["created_at"], job["id"]), reference_id if retry else None,
    )
    if not previous or not previous["question"] or previous["question"] == question:
        return question, None, True
    can_use_hyde = previous["allow_external"] is True and previous["input_level"] < 3
    return f"{previous['question']} {question}", previous["id"], can_use_hyde


def _collect_documents(conn, principal, query, hyde_vector, search_trace) -> list[dict]:
    candidates = search_documents(conn, principal, query, _EVIDENCE_LIMIT, hyde_vector=hyde_vector, trace_out=search_trace)
    return [doc for doc in candidates if not detect_injection(doc["quote"])[0]]


def _document_sources(evidence: list[dict]) -> list[dict]:
    fields = ("kind", "document_id", "version", "acl_version", "tenant_id")
    return [{key: doc[key] for key in fields} for doc in evidence]


def _retrieval_metrics(state: State, search: dict, hyde: dict, retry: bool) -> dict:
    timing = state.get("timing", {})
    totals = {k: timing.get(k, 0) + search.get(k, 0) for k in ("embed_ms", "es_ms", "pg_ann_ms", "rerank_ms")}
    totals["pg_ann_ms"] += search.get("hyde_ann_ms", 0)
    totals["hyde_ms"] = timing.get("hyde_ms", 0) + hyde.get("elapsed_ms", 0)
    degraded = dict(state.get("degraded", {}))
    for key in ("es_fallback", "rerank_fallback", "ann_blocked"):
        degraded[key] = bool(degraded.get(key) or search.get(key))
    degraded["hyde_used"] = bool(degraded.get("hyde_used") or hyde.get("used"))
    degraded["adaptive_retry"] = retry
    update = {"timing": totals, "degraded": degraded}
    if hyde.get("llm_attempts") or hyde.get("llm_errors"):
        update["token_usage"] = merge_token_usage(state.get("token_usage"), "hyde", hyde)
    return update


def retrieve_documents(state: State) -> dict:
    """首轮仅检索可读文档，依据证据量与重排置信度决定后续节点。"""
    principal, job = validate_task_execution(state, "检索授权资料", 25)
    cfg = load_config()
    search, hyde = {}, {}
    with database_connection() as conn:
        query, prior_id, can_use_hyde = _query_context(conn, principal, job, state, retry=False)
        vector = None
        if not cfg.adaptive_hyde and can_use_hyde:
            vector = build_hyde_vector(query, conn=conn, principal=principal, job=job, trace_out=hyde)
        evidence = _collect_documents(conn, principal, query, vector, search)

    sufficient = any(doc.get("score", 0) >= 0.05 or len(doc["quote"]) > 40 for doc in evidence)
    top_score = max((doc["rerank_score"] for doc in evidence if doc.get("rerank_score") is not None), default=None)
    decision: RetrievalDecision = "sufficient_evidence"
    if not sufficient:
        decision = "retry_weak_evidence"
    elif cfg.adaptive_hyde and top_score is not None and top_score < cfg.hyde_rerank_threshold:
        decision = "retry_low_confidence"
    return {
        **_retrieval_metrics(state, search, hyde, retry=False),
        "prior_question_id": prior_id, "evidence": evidence, "sources": _document_sources(evidence),
        "retrieval_decision": decision, "retrieval_search_count": 1,
        "phase": RagPhase.DOCUMENTS_RETRIEVED.value,
    }


def refine_query(state: State) -> dict:
    """按需用新 HyDE 信号或不同关键词补查一次，按两轮排名融合证据。"""
    principal, job = validate_task_execution(state, "补充检索授权资料", 32)
    search, hyde = {}, {}
    with database_connection() as conn:
        query, _, can_use_hyde = _query_context(conn, principal, job, state, retry=True)
        vector = None
        if load_config().adaptive_hyde and can_use_hyde:
            vector = build_hyde_vector(query, conn=conn, principal=principal, job=job, trace_out=hyde)
        candidate = extract_search_keyword(query)
        rewrite = query
        if candidate and not DeterministicController().detect_rewrite_deadlock(candidate, [state["question"]]):
            rewrite = candidate
        search_count = state["retrieval_search_count"]
        evidence = state["evidence"]
        if rewrite != query or vector is not None:
            supplement = _collect_documents(conn, principal, rewrite, vector, search)
            search_count += 1
            evidence = fuse_evidence_rounds(evidence, supplement, _EVIDENCE_LIMIT)
    return {
        **_retrieval_metrics(state, search, hyde, retry=True),
        "evidence": evidence, "sources": _document_sources(evidence), "retrieval_search_count": search_count,
        "phase": RagPhase.QUERY_REFINED.value,
    }


def _business_record(principal, kind: str, identifier: str, fields: tuple[str, ...] | list[str]) -> dict:
    if not identifier:
        message = ("查询实时库存或现价需要商品编号；未接入业务系统时不能提供实时数据" if kind == "stock"
                   else "查询真实订单需要订单编号，并接入企业订单系统")
        return {"available": False, "message": message}
    item = lookup(principal, kind, identifier)
    if not item.get("available"):
        return item
    row = item["record"]
    if not any(field in row for field in fields):
        return {"available": False, "message": "业务系统未返回所需字段。"}
    allowed = {"sku_id" if kind == "stock" else "order_id", "as_of", *fields}
    if "price_minor" in fields and "price_minor" in row:
        allowed.add("currency")
    return {**item, "record": {key: value for key, value in row.items() if key in allowed}}


def fetch_live_data(state: State) -> dict:
    """仅从授权业务系统读取本次请求的字段，保留数据时间与记录标识。"""
    principal, job = validate_task_execution(state, "读取授权业务快照", 38)
    records = []
    missing = []
    fields_by_name = {"available": ("可用库存", "库存"), "price_minor": ("销售价格", "价格"), "status": ("订单状态", "订单状态")}
    other_subjects = fact_subjects(state["question"])
    if "售后" in state["question"] and not other_subjects.intersection({"保修条件", "退换与退款"}):
        other_subjects.add("售后政策")
    for kind, requests in state["live_fields"].items():
        fields = []
        for field, reason in requests.items():
            label, subject = fields_by_name[field]
            other_subjects.discard(subject)
            if reason:
                missing.append(f"{label}：{reason}")
            else:
                fields.append(field)
        if not fields:
            continue
        id_field = "sku_id" if kind == "stock" else "order_id"
        item = _business_record(principal, kind, job["payload"].get(id_field, ""), fields)
        if item.get("available"):
            for field in fields:
                if field not in item["record"]:
                    label = fields_by_name[field][0]
                    missing.append(f"业务系统未返回{label}，无法确认该值。")
        else:
            labels = "、".join(fields_by_name[field][0] for field in fields)
            missing.append(f"{labels}：{item['message']}")
        records.append(item)
    missing.extend(f"本次查询的业务快照不包含{subject}，请另行核对适用的授权资料。" for subject in sorted(other_subjects))
    sources = [item["source"] for item in records if item.get("available")]
    return {
        "business": records, "evidence": [], "sources": sources, "hard_block": not sources,
        "external_blocked": not job["payload"].get("allow_external", True), "missing": missing,
        "phase": RagPhase.LIVE_DATA_LOADED.value,
    }


def prepare_context(state: State) -> dict:
    """汇集授权会话与业务依据，并确定进入专家核验或直接发布。"""
    p, job = validate_task_execution(state, "准备授权证据与业务快照", 38)
    question = state["question"]
    evidence = state["evidence"]
    with database_connection() as conn:
        with node_span("context.recall_conversation", "CHAIN") as span:
            recalled, recalled_sources = get_followup_context(
                conn, p, question, job["input_level"], job.get("conversation_id") or "", before=(job["created_at"], job["id"]),
            )
            if span is not None:
                span.set_attribute("context.evidence_count", len(recalled))
        if recalled:
            seen = {doc["chunk_id"] for doc in evidence}
            extra = [doc for doc in recalled if doc["chunk_id"] not in seen and not detect_injection(doc["quote"])[0]]
            evidence = (extra + evidence)[:_EVIDENCE_LIMIT]

        business = [_business_record(p, kind, identifier, _OVERVIEW_FIELDS[kind])
                    for kind, identifier in requested_business_resources(question, job["payload"]).items()
                    if requests_record_overview(question, kind)]

        sources = _document_sources(evidence)
        sources.extend(item["source"] for item in business if item.get("available"))
        prior_sources = [source for source in recalled_sources if source not in sources]
        sources.extend(prior_sources)
        blocked = (
            not job["payload"].get("allow_external", True)
            or any(not doc["ai_allowed"] for doc in evidence)
            or any(item.get("source", {}).get("level", 1) >= 3 for item in business)
            or (job["input_level"] >= 3 and not (p.boss and job["payload"].get("allow_external", False)))
            or (bool(prior_sources) and not sources_valid(conn, p, prior_sources, external=True))
        )
    hard_block = not evidence and not any(item.get("available") for item in business)
    decision: RetrievalDecision = "run_specialists"
    if state["selected_agents"] == ["finance"] and not any(is_finance_evidence(doc) for doc in evidence):
        decision, hard_block = "no_finance_evidence", True
    elif hard_block:
        decision = "no_authorized_evidence"
    elif blocked:
        decision = "external_processing_blocked"
    update = {
        "evidence": evidence, "sources": sources, "business": business,
        "hard_block": hard_block, "external_blocked": blocked, "retrieval_decision": decision,
        "has_recalled_context": bool(recalled or recalled_sources),
        "degraded": {**state["degraded"], "ann_blocked": not evidence and state["degraded"]["ann_blocked"]},
    }
    if hard_block or blocked:
        if decision == "no_finance_evidence":
            message = "缺少已授权的财务或经营分析资料，不能推测经营数据"
        elif blocked:
            message = "当前资料未获外部 AI 处理许可，无法自动核验。"
        else:
            message = "缺少可靠资料，无法给出事实结论。"
        update.update(missing=[message], review=bool(job["payload"].get("require_review") and not hard_block))
    update["phase"] = RagPhase.CONTEXT_PREPARED.value
    return update
