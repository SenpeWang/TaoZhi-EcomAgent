"""企业知识问答与 ChatBI 受控检索/发布工作流图组装。"""
from __future__ import annotations

import functools
import json
import re
import time

from taozhi.config import load_config
from taozhi.core.memory.service import get_followup_context
from taozhi.core.tools.search_web import extract_search_keyword
from taozhi.domain.errors import Denied
from taozhi.domain.rag_verification import norm_text
from taozhi.domain.text_guard import detect_injection
from taozhi.infrastructure.persistence.access_repo import sources_valid
from taozhi.infrastructure.persistence.bi_repo import get_published_metric_by_id
from taozhi.infrastructure.persistence.job_repo import last_conversation_question, list_recent_completed_ask
from taozhi.knowledge import metrics as metrics_mod
from taozhi.knowledge.business import lookup
from taozhi.knowledge.db import database_connection
from taozhi.knowledge.retriever import build_hyde_vector, search_documents
from taozhi.observability.tracing import node_span
from taozhi.orchestration.controller import DeterministicController
from .intent import plan_node
from .specialists import domain_node, quality_node
from .state import State, empty_token_usage, merge_token_usage, validate_task_execution


def retrieval_node(state: State) -> dict:
    """检索授权文档、执行自适应 HyDE/关键词自愈，并拉取多轮上下文与业务快照。"""
    p, job = validate_task_execution(state, "检索授权资料", 25)
    with database_connection() as c:
        cfg = load_config()
        controller = DeterministicController(max_iterations=2)
        query = state["question"]
        if len(query) <= 12 and re.search(r"^那|它|这个|里面|该|此", query):
            prev_q = last_conversation_question(c, p.tenant_id, p.id, job.get("conversation_id") or "", job["id"])
            if prev_q and prev_q != query:
                query = f"{prev_q} {state['question']}"
        query_history = [state["question"]]
        hyde_trace: dict = {}
        search_traces: list[dict] = []
        hyde_vector = None if cfg.adaptive_hyde else build_hyde_vector(query, conn=c, p=p, job=job, trace_out=hyde_trace)
        tr1: dict = {}
        evidence = [e for e in search_documents(c, p, query, 10, hyde_vector=hyde_vector, trace_out=tr1) if not detect_injection(e["quote"])[0]]
        search_traces.append(tr1)

        is_sufficient = bool(evidence and any(e.get("score", 0) >= 0.05 or len(e.get("quote", "")) > 40 for e in evidence))
        top_rerank = max((e["rerank_score"] for e in evidence if e.get("rerank_score") is not None), default=None)
        low_confidence = top_rerank is not None and top_rerank < cfg.hyde_rerank_threshold
        adaptive_retry = False
        if not is_sufficient or (cfg.adaptive_hyde and low_confidence):
            adaptive_retry = True
            if hyde_vector is None and cfg.adaptive_hyde:
                hyde_vector = build_hyde_vector(query, conn=c, p=p, job=job, trace_out=hyde_trace)
            candidate_kw = extract_search_keyword(query)
            rewrite_q = candidate_kw if (candidate_kw and not controller.detect_rewrite_deadlock(candidate_kw, query_history)) else query
            if rewrite_q != query:
                query_history.append(rewrite_q)
            if rewrite_q != query or hyde_vector is not None:
                tr2: dict = {}
                supplement = [e for e in search_documents(c, p, rewrite_q, 10, hyde_vector=hyde_vector, trace_out=tr2) if not detect_injection(e["quote"])[0]]
                search_traces.append(tr2)
                if supplement:
                    seen_ids = {e["chunk_id"] for e in evidence}
                    for s in supplement:
                        if s["chunk_id"] not in seen_ids:
                            evidence.append(s)
                            seen_ids.add(s["chunk_id"])
                    evidence = evidence[:10]

        recalled, recalled_sources = get_followup_context(c, p, state["question"], job["input_level"], job.get("conversation_id") or "")
        if recalled:
            seen = {e["chunk_id"] for e in evidence}
            evidence = [e for e in recalled if e["chunk_id"] not in seen and not detect_injection(e["quote"])[0]] + evidence
            evidence = evidence[:10]

        business = []
        for kind, key in [("stock", "sku_id"), ("orders", "order_id")]:
            if job["payload"].get(key):
                business.append(lookup(c, p, kind, job["payload"][key]))
        if not job["payload"].get("sku_id") and re.search("实时库存|库存多少|可用库存|现价", state["question"]):
            business.append({"available": False, "message": "查询实时库存或现价需要商品编号；未接入业务系统时不能提供实时数据"})
        if not job["payload"].get("order_id") and re.search("订单状态|查订单|订单发货", state["question"]):
            business.append({"available": False, "message": "查询真实订单需要订单编号，并接入企业订单系统"})

        sources = [{k: e[k] for k in ("kind", "document_id", "version", "acl_version", "tenant_id")} for e in evidence]
        sources.extend(x["source"] for x in business if x.get("available"))
        for reference in recalled_sources:
            if reference not in sources:
                sources.append(reference)

        history = []
        for old in list_recent_completed_ask(c, p.tenant_id, p.id, limit=3):
            if old["result"] and old["input_level"] <= job["input_level"] and sources_valid(c, p, old["sources"], True):
                history.append({"answer": old["result"].get("answer", "")[:500]})

        blocked = (
            not job["payload"].get("allow_external", True)
            or any(not e["ai_allowed"] for e in evidence)
            or any(b.get("source", {}).get("level", 1) >= 3 for b in business)
            or (job["input_level"] >= 3 and not (p.boss and job["payload"].get("allow_external", False)))
        )
        ret_timing = {
            "embed_ms": sum(t.get("embed_ms", 0) for t in search_traces),
            "es_ms": sum(t.get("es_ms", 0) for t in search_traces),
            "pg_ann_ms": sum(t.get("pg_ann_ms", 0) for t in search_traces) + sum(t.get("hyde_ann_ms", 0) for t in search_traces),
            "rerank_ms": sum(t.get("rerank_ms", 0) for t in search_traces),
            "hyde_ms": int(hyde_trace.get("elapsed_ms", 0) or 0),
        }
        degraded = {
            **(state.get("degraded") or {}),
            "es_fallback": any(t.get("es_fallback") for t in search_traces),
            "rerank_fallback": any(t.get("rerank_fallback") for t in search_traces),
            "ann_blocked": (not evidence) and any(t.get("ann_blocked") for t in search_traces),
            "hyde_used": bool(hyde_trace.get("used")),
            "adaptive_retry": adaptive_retry,
        }
        tok_usage = state.get("token_usage") or empty_token_usage()
        if hyde_trace.get("llm_attempts", 0) or hyde_trace.get("llm_errors", 0):
            tok_usage = merge_token_usage(tok_usage, "hyde", hyde_trace)

    return dict(
        evidence=evidence,
        sources=sources,
        business=business,
        history=history,
        external_blocked=blocked,
        hard_block=not evidence and not any(x.get("available") for x in business),
        timing=ret_timing,
        degraded=degraded,
        token_usage=tok_usage,
    )


def data_query_node(state: State) -> dict:
    """问数节点：受控执行语义层指标，SQL 永远由白名单模板组装。"""
    p, job = validate_task_execution(state, "执行受控指标查询（语义层）", 40)
    di = state.get("data_intent") or {}
    result: dict = {"denied": True, "message": di.get("reason") or "未找到可回答该问题的指标。"}
    level = 1
    if di.get("metric_key"):
        with database_connection() as c:
            m = get_published_metric_by_id(c, p.tenant_id, di["metric_id"])
            if not m or not metrics_mod.can_read_metric(c, p, m):
                result = {"denied": True, "message": "该指标不存在，或您当前的角色与密级无权查看。"}
            else:
                try:
                    cols, rows, meta = metrics_mod.execute_metric(c, p, m, di.get("intent") or {})
                    result = {
                        "denied": False,
                        "columns": cols,
                        "rows": metrics_mod.jsonable_rows(rows),
                        "meta": meta,
                        "metric_key": m["metric_key"],
                        "metric_name": m["name"],
                        "unit": m["unit"],
                        "description": m["description"],
                    }
                    level = m["level"]
                except Denied as e:
                    result = {"denied": True, "message": f"指标执行被安全策略拒绝：{e}"}
                except Exception:
                    result = {"denied": True, "message": "指标查询暂时不可用，请稍后重试。"}
    return dict(
        data_result=result,
        evidence=[],
        findings=[],
        missing=[],
        sources=[],
        business=[],
        external_blocked=False,
        hard_block=result.get("denied", False),
        input_level=max(job["input_level"], level),
    )


def publish_node(state: State) -> dict:
    """发布前校验来源并渲染结构化结论、原文受限摘要或业务数据快照。"""
    _, job = validate_task_execution(state, "发布前校验权限与来源", 95)
    if state.get("data_result"):
        dr = state["data_result"]
        if dr.get("denied"):
            answer = dr.get("message", "该问题暂无法通过指标数据回答。") + "\n\n您也可以换个问法，或改从知识库资料中查找。"
        else:
            cols, rows, meta = dr["columns"], dr["rows"], dr["meta"]
            head = "、".join(str(x) for x in cols)
            lines = [
                f"「{dr['metric_name']}」查询结果（{meta.get('unit','')}，共 {len(rows)} 行，耗时 {meta.get('elapsed_ms','?')}ms）：",
                "",
                head,
                "─" * min(60, max(24, len(head) * 2)),
            ]
            lines.extend(" | ".join(str(x) for x in r) for r in rows[:15])
            if len(rows) > 15:
                lines.append(f"...（其余 {len(rows) - 15} 行已省略）")
            lines.extend(
                [
                    "",
                    f"口径说明：{dr['description']}",
                    f"数据来源：企业指标语义层（{dr['metric_key']}），受控只读查询，密级 L{meta.get('level',1)}。",
                ]
            )
            answer = "\n".join(lines)
        return {"answer": answer, "input_level": max(state.get("input_level", job["input_level"]), 1), "hard_block": False, "review": False}

    if state["hard_block"]:
        answer = "当前没有足够的授权资料，无法给出事实结论：相关内容可能未标注、待确认，或您当前无权查看。请补充资料，或联系负责人申请访问权限。"
    elif state["findings"]:
        want_source = bool(re.search(r"来源|出处|哪份|文档|条款|引用|依据", state.get("question") or ""))
        seen_claims = set()
        parts = []
        evs = state.get("evidence") or []
        for f in state["findings"]:
            key = norm_text(f["claim"])
            if key in seen_claims:
                continue
            seen_claims.add(key)
            idx = int(f["evidence_index"])
            cite_tag = f" [{idx + 1}]"
            if want_source and 0 <= idx < len(evs):
                title_txt = str(evs[idx].get("title") or "").strip()
                if title_txt and title_txt not in f["claim"]:
                    cite_tag = f"（来源：《{title_txt}》）[{idx + 1}]"
            parts.append(f"{f['claim']}{cite_tag}")
        answer = "\n\n".join(parts)
    elif state["external_blocked"]:
        answer = "当前资料未获外部 AI 处理许可，以下为授权原文，请人工核验。\n\n" + "\n\n".join(
            e["quote"][:500] + f" [{i + 1}]" for i, e in enumerate(state["evidence"][:5])
        )
    else:
        refs = "；".join(f"《{e.get('title', '')}》" for e in state["evidence"][:3])
        answer = (
            "未能从授权资料中形成核验结论：相关内容在当前资料中可能未标注、待确认。\n"
            + f"已检索到可能相关的资料：{refs}。可换个问法追问细节，或联系资料负责人补充。"
        )
    for b in state["business"]:
        if not b.get("available"):
            answer += "\n\n" + b["message"] + "。"
        else:
            record = b["record"]
            labels = {
                "available": "可用库存",
                "price_minor": "销售价格（分）",
                "status": "订单状态",
                "as_of": "数据时间",
                "sku_id": "商品编号",
                "order_id": "订单编号",
                "currency": "币种",
                "phone": "联系电话",
                "address": "收货地址",
                "customer_name": "客户",
                "purchase_cost_minor": "采购成本（分）",
            }
            statuses = {"CNY": "人民币", "pending": "待支付", "paid": "已支付", "shipped": "已发货", "delivered": "已签收", "cancelled": "已取消", "refunded": "已退款"}
            answer += "\n\n实时业务数据：\n" + "\n".join(f"{labels.get(k, k)}：{statuses.get(str(v), v)}" for k, v in record.items())
    max_level = max([job["input_level"]] + [e["level"] for e in state["evidence"]] + [b.get("source", {}).get("level", 1) for b in state["business"]])
    return {"answer": answer, "input_level": max_level}


def _traced(name: str, func):
    """节点级 span 包装与单节点耗时累加。"""

    @functools.wraps(func)
    def wrapper(state: State):
        attrs = {"job_id": str(state.get("job_id", "")), "run_version": int(state.get("run_version", 0) or 0)}
        with node_span("pipeline." + name, attrs) as span:
            t0 = time.perf_counter()
            result = func(state)
            node_ms = int((time.perf_counter() - t0) * 1000)
            if isinstance(result, dict):
                result["timing"] = {**(state.get("timing") or {}), **(result.get("timing") or {}), f"{name}_ms": node_ms}
            try:
                if span is not None:
                    r = result or {}
                    if r.get("selected_agents") is not None:
                        span.set_attribute("state.selected_agents", ",".join(map(str, r["selected_agents"])))
                    for key in ("evidence", "findings", "missing", "business"):
                        if key in r:
                            span.set_attribute("state." + key, len(r[key] or []))
                    if "answer" in r:
                        span.set_attribute("state.answer_chars", len(r.get("answer") or ""))
                    if "model_calls" in r:
                        span.set_attribute("state.model_calls", int(r.get("model_calls") or 0))
                    if r.get("usage"):
                        span.set_attribute("state.usage", json.dumps(r["usage"], ensure_ascii=False)[:600])
                    if r.get("hard_block") is not None:
                        span.set_attribute("state.hard_block", bool(r.get("hard_block")))
            except Exception:
                pass
            return result

    return wrapper


def build_question_workflow(checkpointer):
    """构建企业知识问答与 ChatBI 受控执行 LangGraph。"""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(State)
    nodes = [
        ("planning", plan_node),
        ("retrieval", retrieval_node),
        ("data_query", data_query_node),
        ("specialists", domain_node),
        ("quality", quality_node),
        ("publication", publish_node),
    ]
    for name, func in nodes:
        builder.add_node(name, _traced(name, func))
    builder.add_edge(START, "planning")
    builder.add_conditional_edges(
        "planning",
        lambda s: "data_query" if s.get("data_intent") else "retrieval",
        {"data_query": "data_query", "retrieval": "retrieval"},
    )
    builder.add_edge("data_query", "publication")
    builder.add_edge("retrieval", "specialists")
    builder.add_edge("specialists", "quality")
    builder.add_edge("quality", "publication")
    builder.add_edge("publication", END)
    return builder.compile(checkpointer=checkpointer)
