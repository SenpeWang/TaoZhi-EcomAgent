"""专职子智能体并行 ReAct 循环执行节点。"""
from __future__ import annotations

import json
import time
from contextlib import ExitStack, closing
from typing import Any, Dict, List, Optional, Tuple

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.core.agent.session import AgentSession
from taozhi.core.tools.outcome import ToolOutcome
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.errors import Denied
from taozhi.domain.research import (
    CLOCK_TOOL,
    SOURCING_TOOLS,
    AgentRunState,
    ResearchScope,
    SubagentTaskSpec,
    ToolCaller,
    classify_research_scope,
    enforce_tool_allowed,
    tools_for_caller,
)
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.server import default_mcp_server
from taozhi.observability.tracing import node_span, record_diagnostic
from .hooks import (
    RuntimeHooks,
    attach_route_delta,
    bump_progress,
    emit_progress,
    get_hooks,
    is_cancelled,
    make_failover_emitter,
)
from .planner import deserialize_task
from .prompts import RESEARCH_PROMPTS
from .task_board import current_task_board
from .schemas import AuditableAgentTransition, EvidenceRecord, _SubagentStepSchema


def _step_arguments(tool_name: str, decision: _SubagentStepSchema, seed: str) -> dict:
    """将本轮决策转换为对应 MCP 工具的参数。"""
    if tool_name == CLOCK_TOOL:
        return {"timezone": decision.timezone} if decision.timezone is not None else {}
    query = (decision.query_or_url or seed).strip()
    if tool_name == "search_1688_wholesale":
        return {"keyword": query}
    if tool_name == "fetch_web_page":
        return {"url": query}
    if tool_name == "search_amazon":
        return {"query": query}
    return {"query": query}


def _execute_guarded_mcp_tool(
    *,
    mcp_client: McpClientAdapter,
    task: SubagentTaskSpec,
    scope: ResearchScope,
    hooks: RuntimeHooks,
    ctx: Any,
    tool_name: str,
    arguments: dict,
    step_idx: int,
    started_at: float,
    timeout_seconds: float,
    tools_records: List[Dict[str, Any]],
    scratch_files: List[str],
    platform_buckets: Dict[str, List[Dict[str, Any]]],
) -> ToolOutcome:
    """在领域白名单与超时保护下调用 MCP 工具、落盘 Scratch 并采集结构化轨迹。"""
    remaining = (timeout_seconds - (time.time() - started_at)) if timeout_seconds > 0 else None
    try:
        with node_span("research.authorize_tool", "CHAIN"):
            enforce_tool_allowed(tool_name, scope, caller=ToolCaller.RESEARCH_WORKER)
    except Denied as exc:
        outcome = ToolOutcome(tool_name=tool_name, status="error", dispatched=False, error_reason=exc.message)
        tools_records.append(
            {
                "tool_name": tool_name,
                "parameters": arguments,
                "dispatched": outcome.dispatched,
                "status": "error",
                "result_summary": exc.message,
                "data_count": 0,
                "elapsed": 0.0,
            }
        )
        return outcome

    bump_progress(hooks, 5, 70, f"[tool_execution_start] [{agent_display_name(task.role)}] 调用工具 {tool_name}")
    outcome = mcp_client.call_tool(
        tool_name,
        arguments,
        remaining_seconds=remaining,
        cancel_event=hooks.cancel_event,
    )
    if tool_name != CLOCK_TOOL and outcome.items:
        scratch_p = ctx.write_scratch(
            f"step_{step_idx}_{tool_name}.json",
            json.dumps(outcome.items, ensure_ascii=False, indent=2),
        )
        scratch_files.append(str(scratch_p))
        if tool_name in ("search_1688_wholesale", "search_amazon"):
            platform_buckets["sourcing"].extend(outcome.items)
        else:
            platform_buckets["web"].extend(outcome.items)

    if outcome.status != "ok":
        summary_txt = f"状态={outcome.status}: {outcome.error_reason or '无匹配数据'}"
    elif tool_name == CLOCK_TOOL:
        reading = outcome.items[0]
        summary_txt = f"{reading['current_time']} ({reading['timezone']})"
    else:
        summary_txt = f"提取 {outcome.data_count} 条结果 (耗时 {outcome.elapsed}s)"
    tools_records.append(
        {
            "tool_name": tool_name,
            "parameters": arguments,
            "dispatched": outcome.dispatched,
            "status": outcome.status,
            "result_summary": summary_txt,
            "data_count": outcome.data_count,
            "elapsed": outcome.elapsed,
        }
    )
    bump_progress(hooks, 8, 75, f"[tool_execution_end] [{agent_display_name(task.role)}] {tool_name} 采集完成 · {summary_txt}")
    return outcome


def _plan_subagent_step(
    *,
    llm: LLMClient,
    task: SubagentTaskSpec,
    keyword: str,
    user_query: str,
    mcp_specs: List[Dict[str, Any]],
    step_idx: int,
    max_steps: int,
    react_observations: List[Dict[str, Any]],
    tools_records: List[Dict[str, Any]],
    last_endpoint: str,
    hooks: RuntimeHooks,
    route_events_out: List[Dict[str, Any]],
) -> Tuple[_SubagentStepSchema, str]:
    """决策子智能体当前 ReAct 步是继续调用工具还是收敛输出结论。"""
    with node_span("context.prepare_subagent", "CHAIN", {
        "context.observation_count": len(react_observations), "context.tool_count": len(mcp_specs),
    }) as span:
        queries_seen = [
            str(observation.get("parameters", {}).get("query") or observation.get("parameters", {}).get("keyword") or "")
            for observation in react_observations
            if observation.get("parameters")
        ]
        system, step_prompt = RESEARCH_PROMPTS.render(
            "research.subagent.step",
            {
                "role_name": agent_display_name(task.role),
                "goal": task.goal,
                "output_format": task.output_format,
                "source_guidance": task.source_guidance,
                "boundaries": task.boundaries,
                "expected_evidence": task.expected_evidence,
                "suggested_tools": list(task.suggested_tools),
                "keyword": keyword,
                "user_query": user_query,
                "mcp_specs": json.dumps(mcp_specs, ensure_ascii=False),
                "seed_queries": list(task.seed_queries),
                "queries_seen": queries_seen,
                "step_idx": step_idx,
                "max_steps": max_steps,
                "observations_json": json.dumps(react_observations, ensure_ascii=False),
            },
        )
        if span is not None:
            span.set_attribute("context.output_chars", len(step_prompt))
    decision_error = None
    step_dec: Optional[_SubagentStepSchema] = None
    with route_audit_scope(preferred_endpoint_id=last_endpoint or None, on_failover=make_failover_emitter(hooks)) as step_rec:
        try:
            raw_step = llm.chat_json(
                prompt=step_prompt,
                schema=_SubagentStepSchema,
                tier=ModelTier.STRONG if task.model_tier == "strong" else ModelTier.FAST,
                system=system or "",
                retries=2,
            )
            if isinstance(raw_step, _SubagentStepSchema):
                step_dec = raw_step
            elif isinstance(raw_step, dict) and raw_step.get("action"):
                step_dec = _SubagentStepSchema.model_validate(raw_step)
        except Exception as exc:
            decision_error = exc
        route_events_out.extend(step_rec.snapshot())
        next_endpoint = step_rec.last_active_endpoint or last_endpoint

    if step_dec is None:
        record_diagnostic("workflow.subagent", "fallback", decision_error)
        available = list(task.suggested_tools) or [spec["name"] for spec in mcp_specs]
        if not tools_records and available:
            step_dec = _SubagentStepSchema(
                action="call_tool",
                thought="调用可用工具开始探索",
                tool_name=available[0],
            )
        else:
            step_dec = _SubagentStepSchema(action="finish", thought="已有工具实测观测，收敛当前结果", findings_summary="")
    return step_dec, next_endpoint


def _synthesize_subagent_summary(
    *,
    llm: LLMClient,
    task: SubagentTaskSpec,
    react_observations: List[Dict[str, Any]],
    tools_records: List[Dict[str, Any]],
    gaps_or_errors: List[str],
    last_endpoint: str,
    hooks: RuntimeHooks,
    route_events_out: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """当 ReAct 轮次耗尽未主动 finish 时，按真实观测聚合结论摘要。"""
    summary = ""
    next_ep = last_endpoint
    if react_observations:
        with route_audit_scope(preferred_endpoint_id=last_endpoint or None, on_failover=make_failover_emitter(hooks)) as synth_rec:
            try:
                system, synth_prompt = RESEARCH_PROMPTS.render(
                    "research.subagent.summary",
                    {
                        "role_name": agent_display_name(task.role),
                        "goal": task.goal,
                        "observations_json": json.dumps(react_observations, ensure_ascii=False),
                    },
                )
                summary = llm.chat(
                    synth_prompt or "",
                    tier=ModelTier.FAST,
                    system=system or "",
                    max_tokens=800,
                ).strip()
            except Exception as exc:
                record_diagnostic("workflow.summary", "fallback", exc)
                summary = ""
            route_events_out.extend(synth_rec.snapshot())
            if synth_rec.last_active_endpoint:
                next_ep = synth_rec.last_active_endpoint
    if not summary:
        total_items = sum(t["data_count"] for t in tools_records if t["tool_name"] != CLOCK_TOOL)
        summary = (
            f"[{agent_display_name(task.role)}] 完成 {len(tools_records)} 次工具调用，累计获取 {total_items} 条原始数据"
            + (f"；存在异常/缺口：{'；'.join(gaps_or_errors)}" if gaps_or_errors else "。")
        )
    return summary, next_ep


def subagent_worker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """受控 ReAct 子智能体节点：观测驱动换词、工具异常隔离、外部文本净化、Scratch 隔离。"""
    task = deserialize_task(state["current_task"])
    hooks = get_hooks(state.get("hook_run_id", ""))
    query = str(state.get("user_query") or "")
    keyword = str(state.get("keyword") or "")
    steering = list(state.get("steering_inbox") or [])
    started_at = float(state.get("started_at") or time.time())
    board = current_task_board()
    if board is not None:
        board.start(task.task_id, task.role)
    timeout_seconds = float(state.get("timeout_seconds") or 180.0)
    max_steps = max(1, int(state.get("max_subagent_steps") or 3))
    rationale = str(state.get("decision_reason") or "")

    scope = classify_research_scope(query, steering)
    mcp_client = McpClientAdapter(server=default_mcp_server)
    mcp_specs = mcp_client.list_tool_specs(tools_for_caller(ToolCaller.RESEARCH_WORKER, scope))
    session = AgentSession(
        conversation_id=task.task_id,
    )
    ctx = session.open_agent_workspace(role=task.role)

    react_observations: List[Dict[str, Any]] = []
    tools_records: List[Dict[str, Any]] = []
    scratch_files: List[str] = []
    platform_buckets: Dict[str, List[Dict[str, Any]]] = {"web": [], "sourcing": []}
    gaps_or_errors: List[str] = []
    key_metrics: List[str] = []
    final_summary = ""
    sub_route_events: List[Dict[str, Any]] = []
    sub_last_ep = ""
    t0 = time.perf_counter()

    with ExitStack() as resources:
        resources.callback(ctx.cleanup)
        llm = resources.enter_context(closing(LLMClient(on_attempt=hooks.model_call_observer(task.role))))
        emit_progress(hooks, f"[agent_start] 启动「{agent_display_name(task.role)}」· 目标：{task.goal}")
        for step_idx in range(1, max_steps + 1):
            if is_cancelled(hooks):
                gaps_or_errors.append("任务中途取消")
                break
            if timeout_seconds > 0 and (time.time() - started_at) >= timeout_seconds:
                gaps_or_errors.append("达到全局时间预算上限")
                break

            step_dec, sub_last_ep = _plan_subagent_step(
                llm=llm,
                task=task,
                keyword=keyword,
                user_query=query,
                mcp_specs=mcp_specs,
                step_idx=step_idx,
                max_steps=max_steps,
                react_observations=react_observations,
                tools_records=tools_records,
                last_endpoint=sub_last_ep,
                hooks=hooks,
                route_events_out=sub_route_events,
            )
            if step_dec.action == "finish":
                final_summary = (step_dec.findings_summary or "").strip()
                key_metrics = [str(m) for m in step_dec.key_metrics if m]
                gaps_or_errors.extend(str(g) for g in step_dec.gaps_or_errors if g)
                break

            tool_name = (step_dec.tool_name or (task.suggested_tools[0] if task.suggested_tools else "")).strip()
            arguments = _step_arguments(tool_name, step_dec, task.seed_queries[0] if task.seed_queries else keyword)
            outcome = _execute_guarded_mcp_tool(
                mcp_client=mcp_client,
                task=task,
                scope=scope,
                hooks=hooks,
                ctx=ctx,
                tool_name=tool_name,
                arguments=arguments,
                step_idx=step_idx,
                started_at=started_at,
                timeout_seconds=timeout_seconds,
                tools_records=tools_records,
                scratch_files=scratch_files,
                platform_buckets=platform_buckets,
            )
            preview = outcome.text_preview
            if len(preview) > 1500:
                with node_span("context.trim_observation", "CHAIN", {"context.input_chars": len(preview)}) as span:
                    preview = preview[:1500]
                    if span is not None:
                        span.set_attribute("context.output_chars", len(preview))
            cleaned_preview, inj_hits = session.after_tool_sanitize(preview)
            if inj_hits:
                gaps_or_errors.append(f"{tool_name} 返回内容触发外部注入警告({','.join(inj_hits)})")
            react_observations.append(
                {
                    "step": step_idx,
                    "tool": tool_name,
                    "parameters": arguments,
                    "status": outcome.status,
                    "data_count": outcome.data_count,
                    "error_reason": outcome.error_reason,
                    "preview": cleaned_preview,
                }
            )
            if outcome.status in ("error", "timeout", "cancelled") and outcome.error_reason:
                gaps_or_errors.append(f"{tool_name}({outcome.status}): {outcome.error_reason}")

        time_references = [t["result_summary"] for t in tools_records if t["tool_name"] == CLOCK_TOOL and t["status"] == "ok"]
        source_records = [t for t in tools_records if t["tool_name"] != CLOCK_TOOL]
        clock_only = bool(tools_records) and not source_records
        if clock_only:
            final_summary = "、".join(time_references) or "未读取到当前时间。"
            key_metrics = []
        elif not final_summary:
            final_summary, sub_last_ep = _synthesize_subagent_summary(
                llm=llm,
                task=task,
                react_observations=react_observations,
                tools_records=tools_records,
                gaps_or_errors=gaps_or_errors,
                last_endpoint=sub_last_ep,
                hooks=hooks,
                route_events_out=sub_route_events,
            )

        elapsed = round(time.perf_counter() - t0, 2)
        channel = "sourcing" if any(t["tool_name"] in SOURCING_TOOLS for t in tools_records) or task.channel_hint == "sourcing" else "web"
        if clock_only:
            channel = "clock"
        relevant = source_records or tools_records
        has_ok = any(t["status"] == "ok" and t["data_count"] > 0 for t in relevant)
        has_err = any(t["status"] in ("error", "timeout", "cancelled") for t in relevant)
        ev_status = "ok" if has_ok else ("error" if has_err else "empty")
        emit_progress(hooks, f"[agent_end] 「{agent_display_name(task.role)}」完成阶段分析 · 状态：{ev_status}（耗时 {elapsed}s）")

        transition: AuditableAgentTransition = {
            "agent_id": ctx.conversation_id,
            "role": task.role,
            "task_id": task.task_id,
            "agent_type": "subagent",
            "state_from": AgentRunState.SPAWNED.value,
            "state_to": (AgentRunState.COMPLETED.value if ev_status != "error" else AgentRunState.PARTIAL_OR_FAILED.value),
            "decision_reason": rationale,
            "tools": tools_records,
            "elapsed": elapsed,
            "scratch_files": scratch_files,
        }
        tool_counts: Dict[str, int] = {}
        for tr in tools_records:
            tname = tr["tool_name"]
            tool_counts[tname] = tool_counts.get(tname, 0) + 1

        evidence: EvidenceRecord = {
            "evidence_id": task.task_id,
            "agent_id": ctx.conversation_id,
            "role": task.role,
            "channel": channel,
            "goal": task.goal,
            "status": ev_status,
            "summary": session.after_tool_sanitize(final_summary)[0],
            "key_metrics": key_metrics,
            "gaps_or_errors": gaps_or_errors,
            "platform_items": {k: v[:20] for k, v in platform_buckets.items() if v},
            "time_references": time_references,
            "tool_counts": tool_counts,
            "elapsed": elapsed,
        }
        if board is not None:
            board_result = {
                "task_id": task.task_id,
                "role": task.role,
                "status": ev_status,
                "summary": evidence["summary"],
                "evidence_ids": [evidence["evidence_id"]],
                "tool_records": tools_records,
                "gaps": gaps_or_errors,
                "elapsed": elapsed,
                "model_tier": task.model_tier,
            }
            if ev_status == "error":
                board.fail(task.task_id, "；".join(gaps_or_errors) or "worker failed", board_result)
            else:
                board.complete(task.task_id, board_result)
        return attach_route_delta({"evidence_pool": [evidence], "audit_trail": [transition]}, sub_route_events, sub_last_ep)
