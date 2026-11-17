"""受派子智能体 ReAct 工具探索执行节点。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.core.agent.context import SubagentContext
from taozhi.core.tools.outcome import ToolOutcome
from taozhi.domain.errors import Denied
from taozhi.domain.research import SOURCING_TOOLS, classify_research_scope, enforce_tool_allowed
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.server import default_mcp_server
from .hooks import attach_route_delta, bump_progress, emit_progress, get_hooks, is_cancelled, make_failover_emitter
from .planner import deserialize_task
from .schemas import AuditableAgentTransition, AuditableToolRecord, EvidenceRecord, _SubagentStepSchema


def _build_tool_kwargs(tool_name: str, query_or_url: str) -> Dict[str, Any]:
    arg = (query_or_url or "").strip()
    if tool_name == "search_web":
        return {"query": arg, "limit": 15}
    if tool_name == "fetch_web_page":
        return {"url": arg, "max_chars": 4000}
    if tool_name == "search_1688_wholesale":
        return {"keyword": arg, "limit": 15}
    if tool_name == "search_amazon":
        return {"query": arg, "limit": 10}
    return {"query": arg}


def subagent_worker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """受派子智能体执行节点：运行独立有界 ReAct 工具探索循环（Think -> Tool Gate -> Observe）。"""
    t0 = time.perf_counter()
    hooks = get_hooks(str(state.get("hook_run_id") or ""))
    task = deserialize_task(state.get("current_task") or {})
    user_query = str(state.get("user_query") or state.get("keyword") or "")
    keyword = str(state.get("keyword") or user_query)
    steering = list(state.get("steering_inbox") or [])
    started_at = float(state.get("started_at") or time.time())
    timeout_seconds = float(state.get("timeout_seconds", 180.0))
    max_steps = max(1, int(state.get("max_subagent_steps", 3)))
    rationale = str(state.get("current_rationale") or task.goal)

    scope = classify_research_scope(user_query, steering)
    ctx = SubagentContext(conversation_id=f"{task.task_id}_{int(time.time())}", role=task.role)
    emit_progress(hooks, f"[agent_handoff] 角色交接: lead_agent ➔ {task.role} · 目标: '{task.goal}'")

    tools_records: List[AuditableToolRecord] = []
    scratch_files: List[str] = []
    platform_items: Dict[str, List[Dict[str, Any]]] = {"web": [], "p1688": [], "amazon": []}
    tool_counts: Dict[str, int] = {}
    react_observations: List[Dict[str, Any]] = []
    final_summary = ""
    key_metrics: List[str] = []
    gaps_or_errors: List[str] = []

    llm = LLMClient()
    sub_route_events: List[Dict[str, Any]] = []
    sub_last_ep = str(state.get("active_llm_endpoint") or "")
    mcp_client = McpClientAdapter(server=default_mcp_server)
    mcp_specs = mcp_client.list_tool_specs(task.allowed_tools)

    def _run_guarded_tool(tool_name: str, query_or_url: str, step_idx: int) -> ToolOutcome:
        try:
            enforce_tool_allowed(tool_name, scope, task.allowed_tools)
        except Denied as exc:
            return ToolOutcome(tool_name=tool_name, status="error", error_reason=exc.message)
        if is_cancelled(hooks):
            return ToolOutcome(tool_name=tool_name, status="cancelled", error_reason="收到任务取消信号，中止工具调用")

        remaining = max(0.0, timeout_seconds - (time.time() - started_at)) if timeout_seconds > 0 else None
        kwargs = _build_tool_kwargs(tool_name, query_or_url)
        emit_progress(hooks, f"[tool_execution_start] [{task.role}] MCP tools/call: {tool_name} · 入参: {json.dumps(kwargs, ensure_ascii=False)}")
        outcome = mcp_client.call_tool(
            tool_name=tool_name,
            arguments=kwargs,
            remaining_seconds=remaining,
            cancel_event=hooks.cancel_event,
        )
        if outcome.items:
            s_path = ctx.write_scratch(f"step{step_idx}_{tool_name}.json", json.dumps(outcome.items, ensure_ascii=False, indent=2))
            scratch_files.append(str(s_path))

        if tool_name == "search_web":
            platform_items["web"].extend(outcome.items)
        elif tool_name == "search_1688_wholesale":
            platform_items["p1688"].extend(outcome.items)
        elif tool_name == "search_amazon":
            platform_items["amazon"].extend(outcome.items)
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + outcome.data_count

        summary_txt = (
            f"状态 {outcome.status} · 返回 {outcome.data_count} 条结果"
            if outcome.status in ("ok", "empty")
            else f"状态 {outcome.status} · {outcome.error_reason}"
        )
        tools_records.append(
            {
                "tool_name": tool_name,
                "parameters": kwargs,
                "status": outcome.status,
                "result_summary": summary_txt,
                "data_count": outcome.data_count,
                "elapsed": outcome.elapsed,
            }
        )
        bump_progress(hooks, 8, 75, f"[tool_execution_end] [{task.role}] 工具 {tool_name} 完毕 · {summary_txt}")
        return outcome

    for step_idx in range(1, max_steps + 1):
        if is_cancelled(hooks):
            gaps_or_errors.append("任务中途取消")
            break
        if timeout_seconds > 0 and (time.time() - started_at) >= timeout_seconds:
            gaps_or_errors.append("达到全局时间预算上限")
            break

        step_prompt = (
            f"你是电商调研子智能体：【{task.role}】。\n"
            f"核心目标：{task.goal}\n"
            f"品类词：【{keyword}】；用户原诉求：{user_query}\n"
            f"授权可用 MCP 工具（tools/list 契约，严禁越界）：\n{json.dumps(mcp_specs, ensure_ascii=False)}\n"
            f"建议起步词：{list(task.seed_queries)}\n"
            f"当前进度：第 {step_idx}/{max_steps} 步。\n"
            f"历史观测记录：\n{json.dumps(react_observations, ensure_ascii=False)}\n\n"
            "请自主决策下一步：\n"
            "- 若尚需收集信息，或上一步 status='empty'（应换同义词/更宽泛核心词再查），或想用 fetch_web_page 深抓上一步返回的某个 url 详情：输出 action='call_tool'，填写合法 tool_name 和 query_or_url。\n"
            "- 若已获取足够实证，或所有渠道均 status='error'/'timeout' 无法继续抓取：输出 action='finish'，并严谨填写 findings_summary、key_metrics 与 gaps_or_errors。\n"
            "【铁律】：严禁编造工具未返回的任何数字或链接。"
        )
        step_dec: Optional[_SubagentStepSchema] = None
        with route_audit_scope(preferred_endpoint_id=sub_last_ep or None, on_failover=make_failover_emitter(hooks)) as step_rec:
            try:
                raw_step = llm.chat_json(
                    prompt=step_prompt,
                    schema=_SubagentStepSchema,
                    tier=ModelTier.FAST,
                    system=f"你是专注【{task.role}】的客观市场调查专员，基于工具实测观测执行 ReAct 循环。",
                    retries=2,
                )
                if isinstance(raw_step, _SubagentStepSchema):
                    step_dec = raw_step
                elif isinstance(raw_step, dict) and raw_step.get("action"):
                    step_dec = _SubagentStepSchema.model_validate(raw_step)
            except Exception:
                step_dec = None
            sub_route_events.extend(step_rec.snapshot())
            if step_rec.last_active_endpoint:
                sub_last_ep = step_rec.last_active_endpoint

        if step_dec is None:
            if not tools_records and task.allowed_tools:
                step_dec = _SubagentStepSchema(
                    action="call_tool",
                    thought="执行起步检索收集原始情报",
                    tool_name=task.allowed_tools[0],
                    query_or_url=task.seed_queries[0] if task.seed_queries else keyword,
                )
            else:
                step_dec = _SubagentStepSchema(action="finish", thought="已有工具实测观测，收敛当前结果", findings_summary="")

        if step_dec.action == "finish":
            final_summary = (step_dec.findings_summary or "").strip()
            key_metrics = [str(m) for m in step_dec.key_metrics if m]
            gaps_or_errors.extend(str(g) for g in step_dec.gaps_or_errors if g)
            break

        tool_name = (step_dec.tool_name or (task.allowed_tools[0] if task.allowed_tools else "")).strip()
        q_arg = (step_dec.query_or_url or (task.seed_queries[0] if task.seed_queries else keyword)).strip()
        outcome = _run_guarded_tool(tool_name, q_arg, step_idx)
        react_observations.append(
            {
                "step": step_idx,
                "tool": tool_name,
                "arg": q_arg,
                "status": outcome.status,
                "data_count": outcome.data_count,
                "error_reason": outcome.error_reason,
                "preview": outcome.text_preview[:1500],
            }
        )
        if outcome.status in ("error", "timeout", "cancelled") and outcome.error_reason:
            gaps_or_errors.append(f"{tool_name}({outcome.status}): {outcome.error_reason}")

    if not final_summary:
        if react_observations:
            with route_audit_scope(preferred_endpoint_id=sub_last_ep or None, on_failover=make_failover_emitter(hooks)) as synth_rec:
                try:
                    synth_prompt = (
                        f"你是【{task.role}】，请仅依据以下工具真实返回观测（严禁虚构），针对目标『{task.goal}』归纳客观事实要点：\n"
                        f"{json.dumps(react_observations, ensure_ascii=False)}"
                    )
                    final_summary = llm.chat(synth_prompt, tier=ModelTier.FAST, system="你是严禁幻觉与造假的市场分析专员。", max_tokens=800).strip()
                except Exception:
                    final_summary = ""
                sub_route_events.extend(synth_rec.snapshot())
                if synth_rec.last_active_endpoint:
                    sub_last_ep = synth_rec.last_active_endpoint
        if not final_summary:
            total_items = sum(t["data_count"] for t in tools_records)
            final_summary = (
                f"[{task.role}] 完成 {len(tools_records)} 次工具调用，累计获取 {total_items} 条原始数据"
                + (f"；存在异常/缺口：{'；'.join(gaps_or_errors)}" if gaps_or_errors else "。")
            )

    elapsed = round(time.perf_counter() - t0, 2)
    channel = "sourcing" if any(t["tool_name"] in SOURCING_TOOLS for t in tools_records) or task.channel_hint == "sourcing" else "web"
    has_ok = any(t["status"] == "ok" and t["data_count"] > 0 for t in tools_records)
    has_err = any(t["status"] in ("error", "timeout") for t in tools_records)
    ev_status = "ok" if has_ok else ("error" if has_err else "empty")
    emit_progress(hooks, f"[agent_end] 【{task.role}】完成探索 · 状态: {ev_status} (耗时 {elapsed}s)")

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": task.role,
        "type_name": task.task_id,
        "state_from": "SPAWNED",
        "state_to": "COMPLETED" if ev_status != "error" else "PARTIAL_OR_FAILED",
        "decision_rationale": rationale,
        "tools": tools_records,
        "elapsed": elapsed,
        "scratch_files": scratch_files,
    }
    evidence: EvidenceRecord = {
        "evidence_id": task.task_id,
        "subagent_id": ctx.conversation_id,
        "role": task.role,
        "channel": channel,
        "goal": task.goal,
        "summary": final_summary,
        "key_metrics": key_metrics,
        "gaps_or_errors": gaps_or_errors,
        "platform_items": platform_items,
        "tool_counts": tool_counts,
        "status": ev_status,
        "elapsed": elapsed,
    }
    return attach_route_delta({"evidence_pool": [evidence], "audit_trail": [transition]}, sub_route_events, sub_last_ep)
