"""市场调研自治多智能体 LangGraph 编排图组装与执行入口。"""
from __future__ import annotations

import time
import uuid
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, START, StateGraph
from openinference.semconv.trace import SpanAttributes
from opentelemetry.trace import StatusCode

from taozhi.core.agent.session import AgentSession
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.research import StopReason
from taozhi.observability.tracing import current_trace_id, node_span, record_diagnostic
from .hooks import RuntimeHooks, emit_progress, normalize_steering_item, register_hooks, unregister_hooks
from .planner import lead_orchestrator_node, orchestrator_router
from .reporter import final_persistence_node, synthesizer_node
from .schemas import AutonomousAgentState
from .subagents import subagent_worker_node



def _traced_research_node(name: str, func: Callable) -> Callable:
    """在并行分支记录节点耗时，按实际运行实例关联调研结果。"""
    @wraps(func)
    def run(state):
        operation = f"research.{name}"
        if name == "final_node":
            with node_span(operation, "CHAIN"):
                return func(state)
        role = state["current_task"]["role"] if name == "subagent_worker" else operation
        with node_span(operation, "AGENT", {SpanAttributes.AGENT_NAME: role}) as span:
            result = func(state)
            if span is not None and name == "orchestrator":
                next_action = result.get("next_action")
                if next_action is not None:
                    span.set_attribute("research.next_action", next_action)
                tasks = result.get("pending_tasks")
                if next_action == "dispatch_subagents" and isinstance(tasks, list):
                    span.set_attribute("research.dispatched_count", len(tasks))
            transition = next(iter(result.get("audit_trail", [])), None)
            if span is not None and transition:
                span.set_attribute("subagent_id", transition["agent_id"])
                if transition["state_to"] == "PARTIAL_OR_FAILED":
                    span.set_attribute("error.type", "ResearchAgentFailure")
                    span.set_status(StatusCode.ERROR)
            return result

    return run


def build_autonomous_agent_graph(checkpointer: Any = None, *, require_checkpointer: bool = False) -> Any:
    """编译真·自治多智能体 Supervisor Loop 状态图。

    Args:
        checkpointer: LangGraph 状态检查点持久化器。
        require_checkpointer: 为 True 时强制断言 ``checkpointer`` 非空。

    Returns:
        编译完成的可执行 LangGraph StateGraph 实例。
    """
    if require_checkpointer and checkpointer is None:
        raise ValueError("启用持久化或中断恢复时必须传入非空 checkpointer（如 PostgresSaver）")
    builder = StateGraph(AutonomousAgentState)
    for name, func in (
        ("orchestrator", lead_orchestrator_node),
        ("subagent_worker", subagent_worker_node),
        ("synthesizer", synthesizer_node),
        ("final_node", final_persistence_node),
    ):
        builder.add_node(name, _traced_research_node(name, func))

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges(
        "orchestrator",
        orchestrator_router,
        ["subagent_worker", "synthesizer", "final_node"],
    )
    builder.add_edge("subagent_worker", "orchestrator")
    builder.add_edge("synthesizer", "final_node")
    builder.add_edge("final_node", END)

    return builder.compile(checkpointer=checkpointer)


def _resume_from_prior_checkpoint(
    *,
    graph: Any,
    checkpointer: Any,
    job_id: str,
    run_version: int,
    max_turns: int,
    init_state: AutonomousAgentState,
    seen_init_ids: set[str],
    hooks: RuntimeHooks,
) -> None:
    """若当前为失败/取消后的断点重试执行，逆序从上一版本检查点继承黑板证据与路由轨迹。"""
    if checkpointer is None or not job_id or run_version <= 1:
        return
    for prev_ver in range(run_version - 1, 0, -1):
        try:
            snap = graph.get_state({"configurable": {"thread_id": f"research_{job_id}_{prev_ver}"}})
            vals = (snap.values or {}) if snap else {}
        except Exception as exc:
            record_diagnostic("storage.checkpoint", "skipped", exc)
            vals = {}
        if vals and (vals.get("evidence_pool") or vals.get("outline") or vals.get("llm_route_history")):
            for list_k in ("evidence_pool", "invalidated_evidence_ids", "outline", "audit_trail", "artifacts", "llm_route_history"):
                if vals.get(list_k):
                    init_state[list_k] = list(vals.get(list_k) or [])
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


def _build_research_result_payload(
    *,
    merged_state: Dict[str, Any],
    keyword: str,
    conversation_id: str,
    total_elapsed: float,
    session: AgentSession,
    hooks: RuntimeHooks,
) -> Dict[str, Any]:
    """聚合并发子智能体证据池与审计轨迹，同时沉淀 Piagent 会话长效记忆。"""
    subagents_trace: List[Dict[str, Any]] = []
    for tr in merged_state.get("audit_trail", []):
        tool_records = tr.get("tools", [])
        subagents_trace.append(
            {
                "id": tr["agent_id"],
                "role": tr["role"],
                "type_name": tr["type_name"],
                "status": tr["state_to"].lower(),
                "decision_rationale": tr["decision_rationale"],
                "elapsed": tr["elapsed"],
                "tools": [
                    {
                        "name": t["tool_name"],
                        "description": t["result_summary"],
                        "status": t.get("status"),
                        "dispatched": t.get("dispatched"),
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
        for ev in active_ev:
            if ev.get("status") == "ok" and ev.get("summary"):
                session.record_fact(
                    subject=keyword,
                    predicate=agent_display_name(str(ev.get("role") or "调研洞察")),
                    obj=str(ev["summary"])[:300],
                    source=str(ev.get("evidence_id") or conversation_id or "research"),
                )
        emit_progress(hooks, f"[agent_end] 协同调研已完成，终审报告已生成（总耗时 {total_elapsed}s）", 100)

    return {
        "report": merged_state.get("report", ""),
        "outline": merged_state.get("outline", []),
        "platforms": platforms_summary,
        "model_calls": hooks.model_calls,
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


def execute_autonomous_agents(
    keyword: str,
    user_query: str = "",
    job_id: str = "",
    tenant_id: str = "default",
    run_version: int = 1,
    conversation_id: str = "",
    checkpointer: Any = None,
    emit_event_fn: Optional[Callable[[str, int], None]] = None,
    initial_steering: Optional[List[Any]] = None,
    timeout_seconds: float = 180.0,
    max_turns: int = 5,
    max_parallel_subagents: int = 4,
    max_subagent_steps: int = 3,
    check_cancel_fn: Optional[Callable[[], bool]] = None,
    fetch_steering_fn: Optional[Callable[[], List[Any]]] = None,
    require_checkpointer: bool = False,
    model_call_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    result_delta_fn: Optional[Callable[[str], None]] = None,
    result_reset_fn: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """在 AgentSession 与检查点保护下启动电商市场自主协同调研系统。

    Args:
        keyword: 目标商品或品类关键词。
        user_query: 用户原始完整提问。
        job_id: 数据库任务主编号（用于组装唯一 LangGraph ``thread_id``）。
        tenant_id: 租户标识符。
        run_version: 当前执行版本号。
        conversation_id: 多轮协同调研会话标识。
        checkpointer: LangGraph PostgreSQL 检查点持久化器。
        emit_event_fn: 运行期阶段进度与审计信标推送回调。
        initial_steering: 启动前已入队的用户动态干预列表。
        timeout_seconds: 调研总时间预算上限（秒）。
        max_turns: 主协调器最大决策轮数。
        max_parallel_subagents: 单轮并发子智能体数量上限。
        max_subagent_steps: 单个子智能体最大 ReAct 步数。
        check_cancel_fn: 轮询检查任务是否已取消的回调。
        fetch_steering_fn: 轮询拉取新追加动态干预指令的回调。
        require_checkpointer: 为 True 时未传入持久化 checkpointer 将快速失败。
        model_call_fn: 可选的逐次模型调用计量回调，独立运行时可省略。
        result_delta_fn: 收取报告分析师运行中生成的可见正文。
        result_reset_fn: 撤回未成功的端点尝试已输出的草稿。

    Returns:
        包含终审 Markdown 研报、平台分布、子智能体审计轨迹及路由记录的结果字典。
    """
    if require_checkpointer and checkpointer is None:
        raise ValueError("启用持久化或中断恢复时必须传入非空 checkpointer（如 PostgresSaver）")
    if (result_delta_fn is None) != (result_reset_fn is None):
        raise ValueError("报告正文和草稿重置回调须同时提供")

    hook_run_id = f"run_{uuid.uuid4().hex[:12]}"
    hooks = RuntimeHooks(
        check_cancel_fn=check_cancel_fn, fetch_steering_fn=fetch_steering_fn,
        emit_fn=emit_event_fn, model_call_fn=model_call_fn,
        result_delta_fn=result_delta_fn, result_reset_fn=result_reset_fn,
    )
    session = AgentSession(
        conversation_id=conversation_id or job_id or hook_run_id,
        tenant_id=tenant_id,
    )

    started_at = time.time()
    init_steering_texts: List[str] = []
    init_steering_ids: List[str] = []
    seen_init_ids: set[str] = set()
    for raw_item in initial_steering or []:
        norm = normalize_steering_item(raw_item)
        if norm is not None and norm[0] not in seen_init_ids:
            seen_init_ids.add(norm[0])
            init_steering_ids.append(norm[0])
            init_steering_texts.append(norm[1])

    init_state: AutonomousAgentState = {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "run_version": run_version,
        "conversation_id": conversation_id,
        "hook_run_id": hook_run_id,
        "keyword": keyword,
        "user_query": user_query or keyword,
        "steering_inbox": init_steering_texts,
        "steering_event_ids": init_steering_ids,
        "processed_steering_count": 0,
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
    _resume_from_prior_checkpoint(
        graph=graph,
        checkpointer=checkpointer,
        job_id=job_id,
        run_version=run_version,
        max_turns=max_turns,
        init_state=init_state,
        seen_init_ids=seen_init_ids,
        hooks=hooks,
    )

    thread_id = f"research_{job_id}_{run_version}" if job_id else f"research_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    emit_progress(hooks, f"[agent_start] {agent_display_name('research.orchestrator')}启动选品调研 · 调研主题：【{keyword}】", 5)

    list_fields = ("steering_inbox", "steering_event_ids", "evidence_pool", "invalidated_evidence_ids", "audit_trail", "llm_route_history", "artifacts")
    merged_state: Dict[str, Any] = dict(init_state)
    for list_key in list_fields:
        merged_state[list_key] = list(init_state[list_key])

    with node_span("worker.research", "CHAIN"):
        register_hooks(hook_run_id, hooks)
        try:
            for chunk in graph.stream(init_state, config=config, stream_mode="updates"):
                for delta in chunk.values():
                    if not isinstance(delta, dict):
                        continue
                    for k, v in delta.items():
                        if k in list_fields and isinstance(v, list):
                            merged_state[k].extend(v)
                        else:
                            merged_state[k] = v
        finally:
            unregister_hooks(hook_run_id)

        result = _build_research_result_payload(
            merged_state=merged_state,
            keyword=keyword,
            conversation_id=conversation_id,
            total_elapsed=round(max(0.0, time.time() - started_at), 1),
            session=session,
            hooks=hooks,
        )
        trace_id = current_trace_id()
        if trace_id:
            result["trace_id"] = trace_id
        return result
