"""动态并行 Orchestrator–Workers 状态图构建与统一执行入口。"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from taozhi.domain.research import StopReason
from .hooks import RuntimeHooks, emit_progress, normalize_steering_item, register_hooks, unregister_hooks
from .planner import lead_orchestrator_node, orchestrator_router
from .reporter import final_persistence_node, synthesizer_node
from .schemas import AutonomousAgentState
from .subagents import subagent_worker_node


def build_autonomous_agent_graph(
    checkpointer: Optional[Any] = None,
    *,
    require_checkpointer: bool = False,
) -> Any:
    """构建动态并行 Orchestrator–Workers 多智能体 LangGraph 状态图。"""
    if require_checkpointer and checkpointer is None:
        raise ValueError("启用持久化或中断恢复时必须传入非空 checkpointer（如 PostgresSaver）")
    builder = StateGraph(AutonomousAgentState)
    builder.add_node("orchestrator", lead_orchestrator_node)
    builder.add_node("subagent_worker", subagent_worker_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("final_node", final_persistence_node)

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges("orchestrator", orchestrator_router, ["subagent_worker", "synthesizer", "final_node"])
    builder.add_edge("subagent_worker", "orchestrator")
    builder.add_edge("synthesizer", "final_node")
    builder.add_edge("final_node", END)
    return builder.compile(checkpointer=checkpointer)


def execute_autonomous_agents(
    keyword: str,
    user_query: str = "",
    job_id: str = "",
    tenant_id: str = "default",
    run_version: int = 1,
    conversation_id: str = "",
    checkpointer: Optional[Any] = None,
    emit_event_fn: Optional[Callable[[str, int], None]] = None,
    initial_steering: Optional[List[Any]] = None,
    timeout_seconds: float = 180.0,
    max_turns: int = 5,
    max_parallel_subagents: int = 4,
    max_subagent_steps: int = 3,
    check_cancel_fn: Optional[Callable[[], bool]] = None,
    fetch_steering_fn: Optional[Callable[[], List[Any]]] = None,
    require_checkpointer: bool = False,
) -> Dict[str, Any]:
    """执行动态并行多智能体调研系统并实时推送各层可审计事件。"""
    if require_checkpointer and checkpointer is None:
        raise ValueError("启用持久化或中断恢复时必须传入非空 checkpointer（如 PostgresSaver）")

    started_at = time.time()
    hook_run_id = f"run_{uuid.uuid4().hex}"
    hooks = RuntimeHooks(check_cancel_fn=check_cancel_fn, fetch_steering_fn=fetch_steering_fn, emit_fn=emit_event_fn)

    init_steering_texts: List[str] = []
    init_steering_ids: List[str] = []
    seen_init_ids = set()
    for raw_item in initial_steering or []:
        norm = normalize_steering_item(raw_item)
        if norm is None:
            continue
        eid, text = norm
        if eid in seen_init_ids:
            continue
        seen_init_ids.add(eid)
        init_steering_ids.append(eid)
        init_steering_texts.append(text)

    register_hooks(hook_run_id, hooks)
    init_state: AutonomousAgentState = {
        "job_id": job_id or f"job_{int(time.time())}",
        "tenant_id": tenant_id,
        "run_version": run_version,
        "conversation_id": conversation_id or f"conv_{int(time.time())}",
        "hook_run_id": hook_run_id,
        "user_query": user_query or keyword,
        "keyword": keyword,
        "steering_inbox": init_steering_texts,
        "steering_event_ids": init_steering_ids,
        "processed_steering_count": 0,
        "system_status": "INIT",
        "next_action": "orchestrator",
        "current_rationale": "激活 Orchestrator-Workers 自治多智能体调研系统",
        "turn_count": 0,
        "max_turns": max_turns,
        "max_parallel_subagents": max_parallel_subagents,
        "max_subagent_steps": max_subagent_steps,
        "started_at": started_at,
        "timeout_seconds": timeout_seconds,
        "cancelled": False,
        "stop_reason": StopReason.CONTINUE.value,
        "outline": [],
        "pending_tasks": [],
        "guard_notices": [],
        "evidence_pool": [],
        "invalidated_evidence_ids": [],
        "audit_trail": [],
        "active_llm_endpoint": "",
        "llm_route_history": [],
        "artifacts": [],
        "report": "",
    }

    graph = build_autonomous_agent_graph(checkpointer=checkpointer, require_checkpointer=require_checkpointer)

    if checkpointer is not None and job_id and run_version > 1:
        for prev_ver in range(run_version - 1, 0, -1):
            try:
                snap = graph.get_state({"configurable": {"thread_id": f"research_{job_id}_{prev_ver}"}})
                vals = (snap.values or {}) if snap else {}
            except Exception:
                vals = {}
            if vals and (vals.get("evidence_pool") or vals.get("outline") or vals.get("llm_route_history")):
                for list_k in ("evidence_pool", "invalidated_evidence_ids", "outline", "audit_trail", "artifacts", "llm_route_history"):
                    if vals.get(list_k):
                        init_state[list_k] = list(vals.get(list_k) or [])  # type: ignore[literal-required]
                if vals.get("active_llm_endpoint"):
                    init_state["active_llm_endpoint"] = str(vals.get("active_llm_endpoint") or "")
                for txt, eid in zip(vals.get("steering_inbox") or [], vals.get("steering_event_ids") or []):
                    if eid not in seen_init_ids:
                        seen_init_ids.add(eid)
                        init_state["steering_inbox"].append(txt)
                        init_state["steering_event_ids"].append(eid)
                init_state["processed_steering_count"] = int(vals.get("processed_steering_count") or 0)
                prior_turn = int(vals.get("turn_count") or 0)
                if prior_turn > 0:
                    init_state["turn_count"] = min(prior_turn, max(0, max_turns - 1))
                emit_progress(hooks, f"[system] [检查点续跑] 已从上一版本检查点恢复 {len(init_state['evidence_pool'])} 份黑板证据与路由状态，继续推进", 5)
                break

    thread_id = f"research_{job_id}_{run_version}" if job_id else f"research_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    emit_progress(hooks, f"[agent_start] 激活自治多智能体主协调代理 (main_agent) · 目标品类: 【{keyword}】", 5)

    list_fields = ("steering_inbox", "steering_event_ids", "evidence_pool", "invalidated_evidence_ids", "audit_trail", "llm_route_history", "artifacts")
    merged_state: Dict[str, Any] = dict(init_state)
    for list_key in list_fields:
        merged_state[list_key] = list(init_state[list_key])

    try:
        for chunk in graph.stream(init_state, config=config, stream_mode="updates"):
            for _, delta in chunk.items():
                if not isinstance(delta, dict):
                    continue
                for k, v in delta.items():
                    if k in list_fields and isinstance(v, list):
                        merged_state[k].extend(v)
                    else:
                        merged_state[k] = v
    finally:
        unregister_hooks(hook_run_id)

    total_elapsed = round(max(0.0, time.time() - started_at), 1)
    subagents_trace: List[Dict[str, Any]] = []
    for tr in merged_state.get("audit_trail", []):
        tool_records = tr.get("tools", [])
        subagents_trace.append(
            {
                "id": tr["agent_id"],
                "role": tr["role"],
                "type": tr["type_name"],
                "type_name": tr["type_name"],
                "state": tr["state_to"].lower(),
                "status": tr["state_to"].lower(),
                "decision_rationale": tr["decision_rationale"],
                "elapsed": tr["elapsed"],
                "tools": [
                    {
                        "name": t["tool_name"],
                        "description": t["result_summary"],
                        "status": t.get("status", "ok"),
                        "data_count": t["data_count"],
                        "parameters": t["parameters"],
                    }
                    for t in tool_records
                ],
                "scratch_files": tr["scratch_files"],
                "brief": tool_records[0]["result_summary"] if tool_records else tr["decision_rationale"],
            }
        )

    invalidated = set(merged_state.get("invalidated_evidence_ids") or [])
    active_ev = [ev for ev in (merged_state.get("evidence_pool") or []) if ev.get("evidence_id") not in invalidated]
    web_items: List[Dict[str, Any]] = []
    p1688_items: List[Dict[str, Any]] = []
    amz_items: List[Dict[str, Any]] = []
    web_briefs: List[str] = []
    sourcing_briefs: List[str] = []

    for ev in active_ev:
        p_items = ev.get("platform_items") or {}
        web_items.extend(p_items.get("web") or [])
        p1688_items.extend(p_items.get("p1688") or [])
        amz_items.extend(p_items.get("amazon") or [])
        if ev.get("channel") == "web" and ev.get("summary"):
            web_briefs.append(str(ev["summary"]))
        elif ev.get("channel") == "sourcing" and ev.get("summary"):
            sourcing_briefs.append(str(ev["summary"]))

    platforms_summary = []
    if web_items or web_briefs:
        platforms_summary.append(
            {
                "name": "web",
                "label": "全网网页实时搜索",
                "state": "ok" if web_items else "empty",
                "state_label": "已完成" if web_items else "无匹配条目",
                "items_count": len(web_items),
                "items": web_items[:20],
                "brief": ("；".join(web_briefs))[:180],
            }
        )
    if p1688_items or amz_items or sourcing_briefs:
        sourcing_count = len(p1688_items) + len(amz_items)
        platforms_summary.append(
            {
                "name": "p1688",
                "label": "1688与亚马逊货源比价",
                "state": "ok" if sourcing_count > 0 else "empty",
                "state_label": "已完成" if sourcing_count > 0 else "无匹配条目",
                "items_count": sourcing_count,
                "items": (p1688_items + amz_items)[:20],
                "brief": ("；".join(sourcing_briefs))[:180],
            }
        )

    if merged_state.get("cancelled"):
        emit_progress(hooks, f"[system] 任务已按用户取消请求提前终止 (耗时 {total_elapsed}s)", 100)
    else:
        emit_progress(hooks, f"[agent_end] 多智能体自主协同达成目标 · 终审研报交付就绪 (总耗时 {total_elapsed}s)", 100)

    return {
        "report": merged_state.get("report", ""),
        "reply": merged_state.get("report", ""),
        "outline": merged_state.get("outline", []),
        "platforms": platforms_summary,
        "model_calls": len(merged_state.get("audit_trail", [])) + int(merged_state.get("turn_count", 0)),
        "elapsed": total_elapsed,
        "artifacts": merged_state.get("artifacts", []),
        "subagents": subagents_trace,
        "audit_trail": merged_state.get("audit_trail", []),
        "active_llm_endpoint": merged_state.get("active_llm_endpoint", ""),
        "llm_route_history": merged_state.get("llm_route_history", []),
        "evidence_pool": active_ev,
        "conversation_id": conversation_id,
        "keyword": keyword,
        "system_status": merged_state.get("system_status", "COMPLETED"),
        "stop_reason": merged_state.get("stop_reason", StopReason.COMPLETED.value),
        "cancelled": bool(merged_state.get("cancelled", False)),
    }
