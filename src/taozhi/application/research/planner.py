"""主协调代理（Lead Research Agent）规划与并行扇出路由。"""
from __future__ import annotations

from contextlib import closing

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from langgraph.types import Send

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.core.tools.search_web import extract_search_keyword
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.research import (
    WEB_TOOLS,
    ResearchBudget,
    ResearchScope,
    StopReason,
    SubagentTaskSpec,
    authorize_subagent_tasks,
    classify_research_scope,
    classify_steering_impact,
    evaluate_hard_stop,
)
from taozhi.domain.text_guard import sanitize
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.server import default_mcp_server
from taozhi.observability.tracing import node_span, record_diagnostic
from .hooks import (
    RuntimeHooks,
    attach_route_delta,
    attach_steering_delta,
    bump_progress,
    collect_new_steering,
    emit_progress,
    get_hooks,
    is_cancelled,
    make_failover_emitter,
)
from .schemas import AutonomousAgentState, _OrchestratorDecisionSchema, _ProposedSubtaskSchema, active_evidence



def default_fallback_tasks(keyword: str, scope: ResearchScope, steering: List[str]) -> List[SubagentTaskSpec]:
    """当调研协调员结构化决策解析失败且黑板尚无证据时的安全起步任务生成。"""
    steer_suffix = f" {' '.join(steering[-2:])}".strip() if steering else ""
    base_query = f"{keyword} {steer_suffix}".strip()
    tasks = [
        SubagentTaskSpec(
            task_id="sub_web_init",
            role="research.market_intelligence",
            goal=f"检索并提炼【{keyword}】的全网主流定价、热销卖点与消费者高频差评痛点",
            allowed_tools=tuple(t for t in WEB_TOOLS if t in scope.allowed_tools),
            seed_queries=(base_query,),
            channel_hint="web",
        )
    ]
    if scope.needs_sourcing:
        if "search_1688_wholesale" in scope.allowed_tools:
            tasks.append(
                SubagentTaskSpec(
                    task_id="sub_1688_init",
                    role="research.sourcing",
                    goal=f"核查【{keyword}】在1688源头工厂的一手出厂价梯度、MOQ起订量与核心产区",
                    allowed_tools=("search_1688_wholesale",),
                    seed_queries=(extract_search_keyword(keyword) or keyword,),
                    channel_hint="sourcing",
                )
            )
        if "search_amazon" in scope.allowed_tools:
            tasks.append(
                SubagentTaskSpec(
                    task_id="sub_amz_init",
                    role="research.competitive_pricing",
                    goal=f"核查【{keyword}】在Amazon全球站的美元定价带、星级评价与跨境机会",
                    allowed_tools=("search_amazon",),
                    seed_queries=(extract_search_keyword(keyword) or keyword,),
                    channel_hint="sourcing",
                )
            )
    return tasks


def serialize_task(spec: SubagentTaskSpec) -> Dict[str, Any]:
    """将领域子任务规约序列化为 LangGraph 可持久化字典。"""
    return {
        "task_id": spec.task_id,
        "role": spec.role,
        "goal": spec.goal,
        "allowed_tools": list(spec.allowed_tools),
        "seed_queries": list(spec.seed_queries),
        "channel_hint": spec.channel_hint,
    }


def deserialize_task(raw: Dict[str, Any]) -> SubagentTaskSpec:
    """将检查点中的任务字典反序列化为领域子任务规约。"""
    return SubagentTaskSpec(
        task_id=str(raw.get("task_id") or f"sub_{uuid.uuid4().hex[:6]}"),
        role=raw["role"],
        goal=str(raw.get("goal") or "收集客观市场事实"),
        allowed_tools=tuple(str(x) for x in (raw.get("allowed_tools") or [])),
        seed_queries=tuple(str(x) for x in (raw.get("seed_queries") or [])),
        channel_hint=str(raw.get("channel_hint") or "general"),
    )


def _build_orchestrator_prompt(
    *,
    keyword: str,
    query: str,
    newly_arrived: List[str],
    all_inbox: List[str],
    notices: List[str],
    mcp_specs: List[Dict[str, Any]],
    remaining_turns: int,
    remaining_sec: float,
    max_parallel_subagents: int,
    active_ev: List[Dict[str, Any]],
) -> str:
    """构造主协调代理本轮调度决策提示词，清洗外部摘要防止间接提示注入。"""
    ev_digest = [
        {
            "evidence_id": ev.get("evidence_id"),
            "role": agent_display_name(ev["role"]),
            "channel": ev.get("channel"),
            "status": ev.get("status"),
            "summary": sanitize(str(ev.get("summary", "")))[:500],
            "key_metrics": ev.get("key_metrics", [])[:8],
            "gaps_or_errors": ev.get("gaps_or_errors", [])[:5],
        }
        for ev in active_ev
    ]
    steer_note = f"最新动态干预指令（必须优先响应）：{newly_arrived}\n" if newly_arrived else ""
    all_steer_note = f"累计干预指令：{all_inbox}\n" if all_inbox else ""
    notice_note = f"上轮领域守卫拦截提示：{notices}\n" if notices else ""
    return (
        f"你是电商选品团队的调研协调员。\n"
        f"目标品类：【{keyword}】\n"
        f"用户完整诉求：{query}\n"
        f"{steer_note}{all_steer_note}{notice_note}"
        f"当前合法允许授权的 MCP 工具列表（tools/list 契约，严禁越界使用未列出工具）：\n{json.dumps(mcp_specs, ensure_ascii=False)}\n"
        f"剩余调度轮次：{remaining_turns}；剩余时间预算：{remaining_sec:.1f}s；单轮最多并发子智能体：{max_parallel_subagents}。\n"
        f"当前黑板已有有效证据包（{len(ev_digest)} 份）：\n{json.dumps(ev_digest, ensure_ascii=False)}\n\n"
        "请自主判断当前决策：\n"
        "1. 若关键维度尚缺、存在口径矛盾或刚收到 Steering 纠偏需要重抓：输出 action='spawn_subagents'，"
        "合理拆解 1 到多个互不重叠的子任务并发执行（比如区分全网痛点、1688源头底价、Amazon海外竞品、交叉核验等），"
        "每个子任务明确 role、goal、allowed_tools（仅从白名单选）与 seed_queries；如旧证据被纠偏推翻，可在 invalidate_evidence_ids 中作废。\n"
        "2. 若已有证据已充分覆盖用户核心问题（且无高价值可补查缺口）：输出 action='finish' 并完善 3-4 条 outline 研报章节大纲。"
    )


def _decide_orchestrator_action(
    *,
    state: AutonomousAgentState,
    hooks: RuntimeHooks,
    prompt: str,
    keyword: str,
    scope: ResearchScope,
    all_inbox: List[str],
    outline: List[str],
    active_ev: List[Dict[str, Any]],
) -> Tuple[_OrchestratorDecisionSchema, List[Dict[str, Any]], str]:
    """调用调研协调员模型获取结构化规划，解析失败时降级至安全规则决策。"""
    planning_error = None
    parsed: Optional[_OrchestratorDecisionSchema] = None
    route_events: List[Dict[str, Any]] = []
    last_ep = ""
    with closing(LLMClient(on_attempt=hooks.model_call_observer("research.orchestrator"))) as llm, route_audit_scope(
        preferred_endpoint_id=str(state.get("active_llm_endpoint") or "") or None,
        on_failover=make_failover_emitter(hooks),
    ) as route_rec:
        try:
            raw_decision = llm.chat_json(
                prompt=prompt,
                schema=_OrchestratorDecisionSchema,
                tier=ModelTier.FAST,
                system="你是客观严谨、负责并行任务分解与证据核查的调研协调员。严禁编造数据。",
                retries=2,
            )
            if isinstance(raw_decision, _OrchestratorDecisionSchema):
                parsed = raw_decision
            elif isinstance(raw_decision, dict) and raw_decision.get("action"):
                parsed = _OrchestratorDecisionSchema.model_validate(raw_decision)
        except Exception as exc:
            planning_error = exc
        route_events = route_rec.snapshot()
        last_ep = route_rec.last_active_endpoint

    if parsed is None:
        record_diagnostic("workflow.plan", "fallback", planning_error)
        if not active_ev:
            parsed = _OrchestratorDecisionSchema(
                action="spawn_subagents",
                thought="调研协调员结构化输出降级，按当前合法工具边界并发启动基础调研子智能体",
                outline=outline or ["市场需求与消费者痛点分析", "产品价格带与竞争格局", "供应链成本与选品建议"],
                subtasks=[
                    _ProposedSubtaskSchema(
                        role=t.role,
                        goal=t.goal,
                        allowed_tools=list(t.allowed_tools),
                        seed_queries=list(t.seed_queries),
                        channel_hint="sourcing" if t.channel_hint == "sourcing" else "web",
                    )
                    for t in default_fallback_tasks(keyword, scope, all_inbox)
                ],
            )
        else:
            parsed = _OrchestratorDecisionSchema(
                action="finish",
                thought="调研协调员结构化输出降级，基于黑板既有有效证据收敛生成研报",
                outline=outline or ["市场需求与竞品格局", "多渠道数据汇总", "选品与操盘建议"],
            )
    return parsed, route_events, last_ep


def lead_orchestrator_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """调研协调员规划节点：吸收 Steering 信号、领域准入授权并动态拆分子任务。"""
    hooks = get_hooks(state.get("hook_run_id", ""))
    keyword = state.get("keyword", "")
    query = state.get("user_query") or keyword
    turn = int(state.get("turn_count", 0)) + 1
    outline = list(state.get("outline") or [])

    steering_delta, steering_id_delta, all_inbox = collect_new_steering(state, hooks)
    processed_cnt = int(state.get("processed_steering_count", 0))
    newly_arrived = all_inbox[processed_cnt:]
    if newly_arrived:
        emit_progress(hooks, f"[steering] 已接收 {len(newly_arrived)} 条补充干预指令，正在动态调整调研计划")

    cancelled = is_cancelled(hooks, bool(state.get("cancelled", False)))
    scope = classify_research_scope(query, all_inbox)
    budget = ResearchBudget(
        max_turns=int(state.get("max_turns", 5)),
        max_parallel_subagents=int(state.get("max_parallel_subagents", 4)),
        max_subagent_steps=int(state.get("max_subagent_steps", 3)),
        timeout_seconds=float(state.get("timeout_seconds", 180.0)),
    )
    started_at = float(state.get("started_at") or time.time())
    elapsed_seconds = max(0.0, time.time() - started_at)
    active_ev = active_evidence(state)

    hard_stop = evaluate_hard_stop(
        turn=turn,
        elapsed_seconds=elapsed_seconds,
        cancelled=cancelled,
        has_report=bool(state.get("report")),
        evidence_count=len(active_ev),
        budget=budget,
    )
    if hard_stop.should_stop:
        res: Dict[str, Any] = {
            "turn_count": turn,
            "outline": outline,
            "next_action": "synthesizer" if hard_stop.force_synthesize else "final_node",
            "pending_tasks": [],
            "guard_notices": [],
            "current_rationale": hard_stop.rationale,
            "system_status": hard_stop.system_status,
            "cancelled": cancelled,
            "stop_reason": hard_stop.stop_reason.value,
            "processed_steering_count": len(all_inbox),
        }
        return attach_steering_delta(res, steering_delta, steering_id_delta)

    mcp_specs = McpClientAdapter(server=default_mcp_server).list_tool_specs(scope.allowed_tools)
    with node_span("context.prepare_coordinator", "CHAIN", {
        "context.evidence_count": len(active_ev), "context.tool_count": len(mcp_specs),
    }) as span:
        prompt = _build_orchestrator_prompt(
            keyword=keyword,
            query=query,
            newly_arrived=newly_arrived,
            all_inbox=all_inbox,
            notices=list(state.get("guard_notices") or []),
            mcp_specs=mcp_specs,
            remaining_turns=max(0, budget.max_turns - turn),
            remaining_sec=max(0.0, budget.timeout_seconds - elapsed_seconds),
            max_parallel_subagents=budget.max_parallel_subagents,
            active_ev=active_ev,
        )
        if span is not None:
            span.set_attribute("context.output_chars", len(prompt))
    parsed, route_events, last_ep = _decide_orchestrator_action(
        state=state,
        hooks=hooks,
        prompt=prompt,
        keyword=keyword,
        scope=scope,
        all_inbox=all_inbox,
        outline=outline,
        active_ev=active_ev,
    )

    if parsed.outline:
        outline = [str(x) for x in parsed.outline[:4]]
    elif not outline:
        outline = ["市场概况与消费者痛点", "竞品价格带与卖点格局", "选品优化与落地建议"]

    invalid_delta = [str(eid) for eid in parsed.invalidate_evidence_ids if eid]
    if newly_arrived and not invalid_delta and active_ev:
        for instr in newly_arrived:
            impact = classify_steering_impact(instr)
            for ev in active_ev:
                ch = ev.get("channel")
                if ((impact.invalidate_web and ch == "web") or (impact.invalidate_wholesale and ch == "sourcing")) and ev["evidence_id"] not in invalid_delta:
                    invalid_delta.append(ev["evidence_id"])

    remaining_valid_count = len([ev for ev in active_ev if ev.get("evidence_id") not in set(invalid_delta)])
    if parsed.action == "finish" and remaining_valid_count > 0:
        emit_progress(hooks, f"[agent_handoff] 已采集 {remaining_valid_count} 项有效证据，转入终审研报合成", 80)
        res = {
            "turn_count": turn,
            "outline": outline,
            "next_action": "synthesizer",
            "pending_tasks": [],
            "guard_notices": [],
            "current_rationale": parsed.thought or "调研协调员确认各维度证据充实，开始合成终审研报",
            "system_status": "REPORTING",
            "cancelled": False,
            "stop_reason": StopReason.CONTINUE.value,
            "processed_steering_count": len(all_inbox),
        }
        if invalid_delta:
            res["invalidated_evidence_ids"] = invalid_delta
        return attach_route_delta(attach_steering_delta(res, steering_delta, steering_id_delta), route_events, last_ep)

    proposed_specs = [
        SubagentTaskSpec(
            task_id=f"t{turn}_sub{idx + 1}_{uuid.uuid4().hex[:4]}",
            role=st.role,
            goal=st.goal,
            allowed_tools=tuple(st.allowed_tools),
            seed_queries=tuple(st.seed_queries or [keyword]),
            channel_hint=st.channel_hint,
        )
        for idx, st in enumerate(parsed.subtasks)
    ]
    if not proposed_specs and not remaining_valid_count:
        proposed_specs = default_fallback_tasks(keyword, scope, all_inbox)

    auth_plan = authorize_subagent_tasks(proposed_specs, scope=scope, budget=budget)
    authorized_tasks = list(auth_plan.tasks)
    new_notices = list(auth_plan.rejected_reasons)

    if not authorized_tasks and remaining_valid_count == 0:
        fallback_plan = authorize_subagent_tasks(default_fallback_tasks(keyword, scope, all_inbox), scope=scope, budget=budget)
        authorized_tasks = list(fallback_plan.tasks)
        new_notices.append("调研协调员原提案均超出当前调研边界，已按允许白名单重置起步任务")

    if not authorized_tasks:
        emit_progress(hooks, "[agent_handoff] 无新增待派发任务，收敛现有证据并转入研报合成", 80)
        next_act = "synthesizer"
        sys_status = "REPORTING"
    else:
        roles_str = " / ".join(agent_display_name(t.role) for t in authorized_tasks)
        bump_progress(hooks, 10, 25, f"[agent_start] 第 {turn} 轮并行调度 {len(authorized_tasks)} 项专职分析（{roles_str}）")
        next_act = "dispatch_subagents"
        sys_status = "STEERING_REDIRECTED" if newly_arrived else "COLLECTING"

    res = {
        "turn_count": turn,
        "outline": outline,
        "next_action": next_act,
        "pending_tasks": [serialize_task(t) for t in authorized_tasks],
        "guard_notices": new_notices,
        "current_rationale": parsed.thought or "调研协调员派发并行子智能体探索市场事实",
        "system_status": sys_status,
        "cancelled": False,
        "stop_reason": StopReason.CONTINUE.value,
        "processed_steering_count": len(all_inbox),
    }
    if invalid_delta:
        res["invalidated_evidence_ids"] = invalid_delta
    return attach_route_delta(attach_steering_delta(res, steering_delta, steering_id_delta), route_events, last_ep)


def orchestrator_router(state: AutonomousAgentState) -> Any:
    """动态扇出路由器：返回并行的 Send(subagent_worker) 列表，或跳转到终审/终止节点。"""
    next_act = state.get("next_action", "final_node")
    if next_act == "dispatch_subagents":
        tasks = state.get("pending_tasks") or []
        if tasks:
            return [
                Send(
                    "subagent_worker",
                    {
                        "current_task": task,
                        "job_id": state.get("job_id", ""),
                        "hook_run_id": state.get("hook_run_id", ""),
                        "user_query": state.get("user_query", ""),
                        "keyword": state.get("keyword", ""),
                        "steering_inbox": list(state.get("steering_inbox") or []),
                        "started_at": state.get("started_at", 0.0),
                        "timeout_seconds": state.get("timeout_seconds", 180.0),
                        "max_subagent_steps": state.get("max_subagent_steps", 3),
                        "current_rationale": state.get("current_rationale", ""),
                    },
                )
                for task in tasks
            ]
        return "synthesizer"
    if next_act == "synthesizer":
        return "synthesizer"
    return "final_node"
