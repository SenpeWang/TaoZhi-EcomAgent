"""智能问答（RAG/ChatBI）、自主协同调研、任务流控制与经营看板应用服务。"""
from __future__ import annotations

import logging
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from taozhi.application.bi import build_bi_workflow, detect_data_intent
from taozhi.application.common.task_guard import empty_token_usage, validate_task_execution
from taozhi.application.rag.workflow import build_rag_workflow
from taozhi.application.research.graph import build_autonomous_agent_graph, execute_autonomous_agents
from taozhi.application.services import document_service
from taozhi.config import get_settings, load_config
from taozhi.core.memory.service import get_memory_store
from taozhi.domain.access import can_review, can_task
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.domain.rag_verification import SPECIALIST_AGENTS
from taozhi.infrastructure.persistence import bi_repo, job_repo
from taozhi.infrastructure.persistence.access_repo import (
    audit,
    batch_sources_validity,
    can_read_metric,
    load_principal,
    sources_valid,
)
from taozhi.knowledge import metrics as metrics_mod
from taozhi.knowledge.business import lookup as business_lookup
from taozhi.knowledge.db import autocommit_connection, database_connection, fetch_one
from taozhi.knowledge.retriever import search_documents
from taozhi.knowledge.semantic_cache import get_semantic_cache
from taozhi.observability.tracing import current_trace_id, node_span

_LOG = logging.getLogger("taozhi.application.task_service")
_CHECKPOINTER_SETUP_LOCK = threading.Lock()
_CHECKPOINTER_SETUP_DSNS: set[str] = set()


@contextmanager
def _postgres_checkpointer(dsn: str | None = None) -> Iterator[Any]:
    """从连接池复用自动提交连接打开 LangGraph PostgresSaver（每个 DSN 仅建表 setup 一次）。"""
    from langgraph.checkpoint.postgres import PostgresSaver

    conn_str = dsn or load_config().dsn
    with autocommit_connection(conn_str) as conn:
        saver = PostgresSaver(conn)
        if conn_str not in _CHECKPOINTER_SETUP_DSNS:
            with _CHECKPOINTER_SETUP_LOCK:
                if conn_str not in _CHECKPOINTER_SETUP_DSNS:
                    saver.setup()
                    _CHECKPOINTER_SETUP_DSNS.add(conn_str)
        yield saver


def _tenant_epoch(conn: Any, tenant_id: str) -> int:
    """读取租户权限/资料变更代际号（tenants.epoch），用于跨进程失效内存语义缓存。"""
    row = fetch_one(conn, "SELECT epoch FROM tenants WHERE id=%s", (tenant_id,))
    return int((row or {}).get("epoch") or 1)


def _diff_ms(start_dt: Any, end_dt: Any = None) -> int:
    """计算两个时间戳之间的毫秒差，兼容时区缺失场景。"""
    if start_dt is None:
        return 0
    if end_dt is None:
        end_dt = datetime.now(timezone.utc) if start_dt.tzinfo else datetime.now()
    elif bool(start_dt.tzinfo) != bool(end_dt.tzinfo):
        end_dt = end_dt.replace(tzinfo=start_dt.tzinfo)
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def submit_task(
    p: Principal,
    *,
    question: str,
    kind: str,
    node_id: str,
    conversation_id: str,
    payload_data: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """提交智能问答或自主协同调研任务，强制由服务端判定密级上限。"""
    node = node_id or next((m["node_id"] for m in p.memberships if m["role"] in ("leader", "employee")), p.root)
    data = dict(payload_data)
    input_level = 3 if p.boss else (2 if p.leader else 1)
    if input_level >= 3 and not p.boss:
        data["allow_external"] = False
    with database_connection() as c:
        job = job_repo.create_job(
            c,
            p,
            kind,
            node,
            data,
            question,
            input_level,
            idempotency_key,
            conversation_id=conversation_id,
        )
        return {"id": job["id"], "task_id": job["id"], "state": job["state"], "message": "任务已提交"}


def list_tasks(p: Principal) -> list[dict[str, Any]]:
    """列出当前用户有权限查看的问答与调研任务（批量预检验来源以消除 N+1 查询）。"""
    with database_connection() as c:
        rows = job_repo.list_ask_and_research_jobs(c, p.tenant_id)
        permitted = [r for r in rows if can_task(p, r)]
        validity = batch_sources_validity(c, p, permitted)
        return [
            job_repo.present_job(c, p, r, False, valid_override=validity.get(r["id"], False))
            for r in permitted
            if r["owner_id"] == p.id or validity.get(r["id"], False)
        ]


def get_task(p: Principal, task_id: str) -> dict[str, Any]:
    """按编号读取任务详情及完整事件流。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        return job_repo.present_job(c, p, r)


def cancel_task(p: Principal, task_id: str) -> dict[str, str]:
    """请求取消正在排队、执行或待审核中的任务。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r or not (r["owner_id"] == p.id or p.boss):
            raise Denied()
        if r["state"] not in ("queued", "running", "review"):
            raise Denied("任务已经结束", "CONFLICT", 409)
        job_repo.mark_job_cancelled(c, task_id)
        audit(c, p, "取消任务", task_id, r["node_id"], r["input_level"])
    return {"message": "已请求取消"}


def steer_task(p: Principal, task_id: str, *, text: str, request_id: str = "") -> dict[str, Any]:
    """向运行中的协同调研任务注入幂等中途干预指令。"""
    steer_id = request_id.strip() or f"steer_{secrets.token_hex(8)}"
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        if r["state"] not in ("queued", "running"):
            raise Denied("只能对进行中的任务追加干预指令", "CONFLICT", 409)
        if not (r["owner_id"] == p.id or p.boss or p.system_admin):
            raise Denied("无权干预该任务", "FORBIDDEN", 403)
        existing_items = ((r.get("payload") or {}).get("steering_inputs")) or []
        if any(isinstance(item, dict) and str(item.get("id") or "") == steer_id for item in existing_items):
            return {"message": "中途干预指令已受理（幂等命中）", "task_id": task_id, "input": text, "request_id": steer_id}
        run_ver = r.get("run_version") or 1
        job_repo.append_steering_to_job(c, p.tenant_id, task_id, run_ver, r.get("progress") or 50, steer_id, text)
        audit(c, p, "中途动态干预", task_id, r["node_id"], r["input_level"], detail={"request_id": steer_id})

    try:
        with _postgres_checkpointer() as saver:
            graph = build_autonomous_agent_graph(checkpointer=saver, require_checkpointer=True)
            thread_id = f"research_{task_id}_{run_ver}"
            cfg = {"configurable": {"thread_id": thread_id}}
            snap = graph.get_state(cfg)
            seen_ids = set((snap.values or {}).get("steering_event_ids") or []) if snap else set()
            if steer_id not in seen_ids:
                graph.update_state(cfg, {"steering_inbox": [text], "steering_event_ids": [steer_id]})
    except Exception as exc:
        _LOG.warning("LangGraph 检查点即时注入失败（已由 DB 队列兜底）: %s", exc)
    return {"message": "中途干预指令已发送并生效", "task_id": task_id, "input": text, "request_id": steer_id}


def retry_task(p: Principal, task_id: str) -> dict[str, Any]:
    """递增执行版本号并重新投递任务入队。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        if not (r["owner_id"] == p.id or p.boss or p.system_admin):
            raise Denied("无权操作此任务", "FORBIDDEN", 403)
        if r["state"] == "running":
            raise Denied("任务正在执行中，无需重复发起", "CONFLICT", 409)
        new_ver = (r["run_version"] or 1) + 1
        job_repo.requeue_job(c, p.tenant_id, task_id, new_ver)
        audit(c, p, "重新发起任务", task_id, r["node_id"], r["input_level"])
    return {"message": "任务已重新排队执行", "task_id": task_id, "run_version": new_ver}


def review_task(p: Principal, task_id: str, *, approve: bool, comment: str) -> dict[str, str]:
    """审核待人工复核的业务答案并校验双方权限及来源时效。"""
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r or r["state"] != "review":
            raise Denied("任务不在待审核状态", "CONFLICT", 409)
        if not can_review(p, r) or (r["owner_id"] == p.id and not p.boss):
            raise Denied("需要对应范围的另一位负责人审核")
        owner = load_principal(c, r["owner_id"])
        if not sources_valid(c, p, r["sources"]) or not sources_valid(c, owner, r["sources"]):
            raise Denied("来源或权限已变化，请重新查询", "STALE", 409)
        if approve and (r["result"] or {}).get("hard_block"):
            raise Denied("缺少可靠来源，不能审核为事实", "QUALITY_BLOCK", 409)
        job_repo.save_task_review(c, p.tenant_id, r["id"], p.id, approve, comment)
        audit(
            c,
            p,
            "审核业务答案",
            r["id"],
            r["node_id"],
            r["input_level"],
            detail={"decision": "approved" if approve else "rejected"},
        )
    return {"message": "答案已发布" if approve else "答案未通过审核"}


def poll_task_events(p: Principal, task_id: str, after_id: int) -> tuple[bool, list[dict[str, Any]], dict[str, Any] | None]:
    """读取单次轮询周期内的增量任务事件与终态摘要，供 SSE 端点推送。"""
    with database_connection() as c:
        job = job_repo.get_job(c, p.tenant_id, task_id)
        if not job or not can_task(p, job):
            return False, [], None
        rows = job_repo.list_task_events(c, p.tenant_id, task_id, after_id=after_id)
        terminal = None
        if job["state"] in ("completed", "review", "failed", "cancelled", "rejected"):
            terminal = {k: job[k] for k in ("id", "state", "stage", "progress", "error")}
        return True, rows, terminal


def list_research_conversations(p: Principal) -> list[dict[str, Any]]:
    """列出用户个人的多轮市场调研会话摘要清单。"""
    with database_connection() as c:
        rows = job_repo.list_research_conversation_summaries(c, p.tenant_id, p.id)
        return [
            {
                "conversation_id": r["conversation_id"],
                "title": (r["first_payload"] or {}).get("keyword") or r["title"],
                "turns": r["turns"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else "",
                "last_active": r["last_active"].isoformat() if r["last_active"] else "",
                "latest_state": r["latest_state"],
            }
            for r in rows
        ]


def get_research_conversation(p: Principal, conversation_id: str) -> dict[str, Any]:
    """读取单个调研会话的全部轮次详情与长期记忆三元组。"""
    with database_connection() as c:
        rows = job_repo.list_research_conversation_turns(c, p.tenant_id, p.id, conversation_id)
        if not rows:
            return {"conversation_id": conversation_id, "turns": [], "context_summary": "", "memory_facts": []}
        turns = []
        for r in rows:
            pj = job_repo.present_job(c, p, r, True)
            evts = job_repo.list_task_events(c, p.tenant_id, r["id"])
            pj["thinking_logs"] = [e["stage"] for e in evts if e.get("stage")]
            calc_elapsed = (
                round((r["finished_at"] - r["started_at"]).total_seconds(), 1)
                if r.get("finished_at") and r.get("started_at")
                else 0
            )
            pj["elapsed"] = (r.get("result") or {}).get("elapsed") or calc_elapsed
            turns.append(pj)
        latest_res = rows[-1].get("result") or {}
        kw = (rows[0].get("payload") or {}).get("keyword") or rows[0]["question"]
    raw_facts = get_memory_store(tenant_id=p.tenant_id).query_facts(subject=kw)[:20]
    facts = []
    for f in raw_facts:
        f_copy = dict(f)
        if not getattr(p, "boss", False) and not getattr(p, "root", False) and isinstance(f_copy.get("object"), str):
            f_copy["object"] = redact(f_copy["object"])
        facts.append(f_copy)
    return {
        "conversation_id": conversation_id,
        "keyword": kw,
        "turns": turns,
        "context_summary": latest_res.get("context_summary", ""),
        "context_tokens": latest_res.get("context_tokens", 0),
        "is_compacted": latest_res.get("is_compacted", False),
        "memory_facts": facts,
    }


def delete_research_conversation(p: Principal, conversation_id: str) -> dict[str, str]:
    """删除个人调研会话及其全部任务与事件流记录。"""
    with database_connection() as c:
        job_repo.delete_research_conversation_records(c, p.tenant_id, p.id, conversation_id)
        audit(c, p, "删除调研对话", conversation_id, p.root, 1)
    return {"message": "对话已成功删除"}


def execute_tool(p: Principal, *, name: str, query: str, identifier: str) -> dict[str, Any]:
    """执行前端直接调用的受控业务查询或企业文档检索工具。"""
    with database_connection() as c:
        if name in ("query_stock", "query_order"):
            return business_lookup(c, p, "stock" if name == "query_stock" else "orders", identifier)
        hits = search_documents(c, p, query, 8)
        return {"items": hits}


def get_dashboard_summary(p: Principal) -> dict[str, Any]:
    """汇总工作台文档、问答任务与模型/业务系统就绪度统计。"""
    docs = document_service.list_documents(p)
    with database_connection() as c:
        jobs = job_repo.list_ask_jobs(c, p.tenant_id)
        permitted = [r for r in jobs if can_task(p, r)]
        validity = batch_sources_validity(c, p, permitted)
        visible = [r for r in permitted if validity.get(r["id"], False)]
    settings = get_settings()
    return dict(
        documents=len(docs),
        tasks=len(visible),
        review=sum(r["state"] == "review" for r in visible),
        completed=sum(r["state"] == "completed" for r in visible),
        teams=[p.nodes[m["node_id"]]["name"] for m in p.memberships if m["role"] != "boss"],
        model_ready=bool(settings.api_key),
        business_ready=bool(settings.business_api_url and settings.business_api_token),
        mode=load_config().mode,
    )


def get_bi_dashboard(p: Principal, days: int) -> dict[str, Any]:
    """执行指标语义层受控查询，构造数据中台经营看板视图。"""

    def run(key: str, intent: dict | None = None) -> dict[str, Any] | None:
        with database_connection() as c:
            m = bi_repo.get_published_metric_by_key(c, p.tenant_id, key)
            if not m or not can_read_metric(c, p, m):
                return None
            try:
                cols, rows, meta = metrics_mod.execute_metric(c, p, m, intent or {})
                return dict(
                    columns=cols,
                    rows=metrics_mod.jsonable_rows(rows),
                    meta=meta,
                    name=m["name"],
                    unit=m["unit"],
                    level=m["level"],
                    description=m["description"],
                )
            except Exception:
                return None

    def total(block: dict[str, Any] | None) -> float | None:
        if not block or not block["rows"]:
            return None
        try:
            return round(sum(float(r[-1]) for r in block["rows"] if isinstance(r[-1], (int, float))), 2)
        except (TypeError, ValueError):
            return None

    window = {"time_range": {"days": days}}
    gsv, orders = run("biz.gsv", window), run("biz.order_count", window)
    rate, profit = run("biz.refund_rate", window), run("biz.gross_profit", window)
    low, aftersales = run("biz.low_stock_count"), run("biz.aftersales_count", window)
    trend = run("biz.gsv_daily", window)

    with database_connection() as c:
        latest_order_at = bi_repo.get_latest_order_at(c, p.tenant_id)

    cards: dict[str, Any] = dict(
        gsv_30d=total(gsv),
        orders_30d=total(orders),
        gross_profit_30d=total(profit),
        low_stock=total(low),
        aftersales_count=total(aftersales),
    )
    if rate and rate["rows"]:
        worst = max(rate["rows"], key=lambda r: float(r[-1]) if isinstance(r[-1], (int, float)) else 0)
        cards["worst_refund_channel"] = {"channel": worst[0], "rate": worst[-1]}
        cards["avg_refund_rate"] = round(sum(float(r[-1]) for r in rate["rows"]) / len(rate["rows"]), 2)

    channel_gsv = None
    if gsv and gsv["rows"]:
        agg: dict[str, float] = {}
        for r in gsv["rows"]:
            agg[r[0]] = round(agg.get(r[0], 0) + float(r[-1]), 2)
        channel_gsv = dict(sorted(agg.items(), key=lambda x: -x[1]))

    category_summary = None
    if gsv and gsv["rows"]:
        cat_gsv: dict[str, float] = {}
        for r in gsv["rows"]:
            cat_gsv[r[1]] = round(cat_gsv.get(r[1], 0) + float(r[-1]), 2)
        cat_profit: dict[str, float] = {}
        if profit and profit["rows"]:
            for r in profit["rows"]:
                cat_profit[r[1]] = round(cat_profit.get(r[1], 0) + float(r[-1]), 2)
        category_summary = [
            dict(
                category=cat,
                gsv=val,
                gross_profit=cat_profit.get(cat) if profit and profit["rows"] else None,
            )
            for cat, val in sorted(cat_gsv.items(), key=lambda x: -x[1])
        ]

    return dict(
        days=days,
        latest_order_at=latest_order_at,
        cards=cards,
        trend=trend,
        channel_gsv=channel_gsv,
        category_summary=category_summary,
        refund_rate=rate,
        aftersales=aftersales,
        low_stock=low,
        gross_profit=profit,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _try_answer_from_semantic_cache(job: dict[str, Any], cache: Any, queue_wait_ms: int) -> bool:
    """检索语义缓存并在来源权限实时有效时直接回填问答终态（同步租户跨进程代际水位）。"""
    with database_connection() as c:
        p = load_principal(c, job["owner_id"])
        epoch = _tenant_epoch(c, job["tenant_id"])
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

    with database_connection() as c:
        if not (cached.get("sources") and sources_valid(c, p, cached.get("sources", []))):
            return False
        e2e_ms = _diff_ms(job.get("created_at"))
        result = dict(
            answer=cached["answer"],
            citations=cached["citations"],
            specialists=[{"name": "Main 调度 / 语义缓存", "count": 1}],
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
        job_repo.complete_job(
            c,
            tenant_id=p.tenant_id,
            job_id=job["id"],
            run_version=job["run_version"],
            state="completed",
            stage="已完成（命中语义缓存）",
            result=result,
            sources=cached["sources"],
            input_level=job["input_level"],
        )
        return True


def _build_bi_result(
    *,
    state: dict[str, Any],
    queue_wait_ms: int,
    e2e_ms: int,
    trace_id: str | None,
) -> dict[str, Any]:
    """将 ChatBI 问数工作流终态组装为前端可消费的结果负载。"""
    timing = dict(state.get("timing") or {})
    timing["queue_wait_ms"] = queue_wait_ms
    timing["e2e_ms"] = e2e_ms
    dr = state.get("data_result") or {}
    result: dict[str, Any] = dict(
        answer=state["answer"],
        citations=[],
        specialists=[{"name": "指标语义层（受控 SQL）", "count": 1}] if (dr and not dr.get("denied")) else [],
        missing=[],
        hard_block=bool(state.get("hard_block")),
        external_blocked=False,
        model_calls=0,
        usage=[],
        cache_hit=False,
        timing=timing,
        token_usage=state.get("token_usage") or empty_token_usage(),
        degraded={},
    )
    if dr and not dr.get("denied"):
        result["data"] = dr
    if trace_id:
        result["trace_id"] = trace_id
    return result


def _build_question_result(
    *,
    state: dict[str, Any],
    citations: list[dict[str, Any]],
    queue_wait_ms: int,
    e2e_ms: int,
    trace_id: str | None,
) -> dict[str, Any]:
    """将 RAG 问答工作流终态裁剪组装为前端与报告可消费的结果负载。"""
    timing = dict(state.get("timing") or {})
    timing["queue_wait_ms"] = queue_wait_ms
    timing["e2e_ms"] = e2e_ms
    token_usage = state.get("token_usage") or empty_token_usage()
    result = dict(
        answer=state["answer"],
        citations=citations,
        specialists=[
            {"name": SPECIALIST_AGENTS[r][0], "count": sum(f["role"] == SPECIALIST_AGENTS[r][0] for f in state["findings"])}
            for r in state["selected_agents"]
        ],
        missing=state["missing"],
        hard_block=state["hard_block"],
        external_blocked=state["external_blocked"],
        model_calls=state["model_calls"],
        usage=state["usage"],
        cache_hit=False,
        timing=timing,
        token_usage=token_usage,
        degraded=state.get("degraded") or {},
    )
    if trace_id:
        result["trace_id"] = trace_id
    return result


def process_question_job(job: dict[str, Any]) -> None:
    """按意图分发至独立工作流：ChatBI 受控问数（application/bi）或知识库 RAG（application/rag）。"""
    cache = get_semantic_cache()
    queue_wait_ms = _diff_ms(job.get("created_at"), job.get("started_at"))
    if _try_answer_from_semantic_cache(job, cache, queue_wait_ms):
        return

    data_intent: dict[str, Any] | None = None
    try:
        with database_connection() as c:
            p_init = load_principal(c, job["owner_id"])
            data_intent = detect_data_intent(c, p_init, job["question"])
    except Exception:
        data_intent = None

    is_bi = bool(data_intent)
    with _postgres_checkpointer() as saver:
        if is_bi:
            flow = build_bi_workflow(saver)
            with node_span("worker.bi", {"job_id": job["id"], "kind": "ask"}):
                state = flow.invoke(
                    {"job_id": job["id"], "run_version": job["run_version"], "data_intent": data_intent},
                    {"configurable": {"thread_id": f"{job['id']}:{job['run_version']}"}},
                )
                trace_id = current_trace_id()
        else:
            flow = build_rag_workflow(saver)
            with node_span("worker.question", {"job_id": job["id"], "kind": "ask"}):
                state = flow.invoke(
                    {"job_id": job["id"], "run_version": job["run_version"]},
                    {"configurable": {"thread_id": f"{job['id']}:{job['run_version']}"}},
                )
                trace_id = current_trace_id()
    validate_task_execution(state, "最终权限核验", 98)
    with database_connection() as c:
        row = job_repo.get_job_by_id(c, job["id"], for_update=True)
        if not row or row["state"] != "running" or row["run_version"] != job["run_version"] or row["cancel_requested"]:
            raise Denied("任务执行版本已失效", "STALE", 409)
        p = load_principal(c, row["owner_id"])
        sources = [] if is_bi else state["sources"]
        if p.version != row["owner_version"] or not sources_valid(c, p, sources):
            raise Denied("人员或来源权限已变化", "SOURCE_CHANGED", 409)
        e2e_ms = _diff_ms(row.get("created_at") or job.get("created_at"))
        if is_bi:
            citations = []
            result = _build_bi_result(state=state, queue_wait_ms=queue_wait_ms, e2e_ms=e2e_ms, trace_id=trace_id)
            status, stage = "completed", "已完成"
        else:
            citations = [{k: e[k] for k in ("document_id", "version", "title", "quote", "page", "chunk_id")} for e in state["evidence"]]
            result = _build_question_result(
                state=state,
                citations=citations,
                queue_wait_ms=queue_wait_ms,
                e2e_ms=e2e_ms,
                trace_id=trace_id,
            )
            status = "review" if state["review"] else "completed"
            stage = "等待业务负责人审核" if state["review"] else "已完成"
        job_repo.complete_job(
            c,
            tenant_id=p.tenant_id,
            job_id=job["id"],
            run_version=job["run_version"],
            state=status,
            stage=stage,
            result=result,
            sources=sources,
            input_level=state["input_level"],
        )
        epoch = _tenant_epoch(c, p.tenant_id)
        if status == "completed":
            job_repo.delete_checkpoint_threads(c, [f"{job['id']}:{job['run_version']}"])

    if not is_bi and not state.get("hard_block") and not state.get("review") and state.get("answer"):
        try:
            cache.put(
                job["tenant_id"],
                job["question"],
                state["answer"],
                citations,
                sources,
                state.get("input_level", job["input_level"]),
                redacted=not p.boss,
                epoch=epoch,
            )
        except Exception as exc:
            _LOG.warning("写入问答语义缓存失败 (%s): %s", job["id"], exc)


def process_research_job(job: dict[str, Any]) -> None:
    """运行多智能体市场调研主副协作图并回传终态报告。"""

    def emit(stage: str, progress: int) -> None:
        with database_connection() as c:
            job_repo.update_job_stage(c, job["tenant_id"], job["id"], job["run_version"], stage, progress)

    def check_cancel() -> bool:
        with database_connection() as c:
            r = job_repo.get_job_by_id(c, job["id"])
            return not r or r["state"] != "running" or r["run_version"] != job["run_version"] or bool(r["cancel_requested"])

    def fetch_steering() -> list:
        with database_connection() as c:
            r = job_repo.get_job_by_id(c, job["id"])
            return list(((r or {}).get("payload") or {}).get("steering_inputs") or [])

    cfg = load_config()
    settings = get_settings()
    conv_id = job.get("conversation_id") or (job.get("payload") or {}).get("conversation_id") or ""
    keyword = (job.get("payload") or {}).get("keyword") or job["question"]
    initial_steering = (job.get("payload") or {}).get("steering_inputs") or []

    with _postgres_checkpointer(cfg.dsn) as saver:
        result = execute_autonomous_agents(
            keyword=keyword,
            user_query=job["question"],
            job_id=job["id"],
            tenant_id=job.get("tenant_id") or "default",
            run_version=job.get("run_version") or 1,
            conversation_id=conv_id,
            checkpointer=saver,
            require_checkpointer=True,
            emit_event_fn=emit,
            initial_steering=initial_steering,
            timeout_seconds=float(settings.llm_timeout or 180.0),
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
        row = job_repo.get_job_by_id(c, job["id"], for_update=True)
        if not row or row["state"] != "running" or row["run_version"] != job["run_version"] or row["cancel_requested"]:
            raise Denied("任务执行版本已失效", "STALE", 409)
        job_repo.complete_job(
            c,
            tenant_id=job["tenant_id"],
            job_id=job["id"],
            run_version=job["run_version"],
            state="completed",
            stage=stage_title,
            result=result,
            sources=[],
        )
