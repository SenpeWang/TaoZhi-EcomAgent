"""协调员直接工具节点：执行一次轻量核验，证据回写黑板后回到协调员。"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from taozhi.core.agent.session import AgentSession
from taozhi.core.tools.outcome import ToolOutcome
from taozhi.domain.errors import Denied
from taozhi.domain.research import (
    AgentRunState,
    ResearchStatus,
    StopReason,
    ToolCaller,
    classify_research_scope,
    enforce_tool_allowed,
    tools_for_caller,
)
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.server import default_mcp_server
from taozhi.observability.tracing import record_diagnostic
from .hooks import emit_progress, get_hooks, is_cancelled
from .task_board import current_task_board
from .schemas import AutonomousAgentState, AuditableAgentTransition, EvidenceRecord


def _arguments(state: AutonomousAgentState) -> Dict[str, Any]:
    """根据工具名构造参数；未知工具交给守卫拒绝。"""
    tool_name = str(state.get("tool_name") or "")
    query_or_url = str(state.get("query_or_url") or "").strip()
    if tool_name == "get_current_time":
        timezone = state.get("timezone")
        return {"timezone": timezone} if timezone is not None else {}
    if tool_name == "fetch_web_page":
        return {"url": query_or_url}
    return {"query": query_or_url}


def coordinator_tool_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """执行协调员的一次轻量工具调用，并写入证据与审计。"""
    hooks = get_hooks(state.get("hook_run_id", ""))
    query = str(state.get("user_query") or state.get("keyword") or "")
    tool_name = str(state.get("tool_name") or "").strip()
    current_calls = int(state.get("coordinator_tool_calls", 0))
    started = time.perf_counter()

    if is_cancelled(hooks, bool(state.get("cancelled", False))):
        return {
            "cancelled": True,
            "system_status": ResearchStatus.CANCELLED.value,
            "stop_reason": StopReason.CANCELLED.value,
        }

    scope = classify_research_scope(query, list(state.get("steering_inbox") or []))
    board = current_task_board()
    task_key = f"coordinator_{current_calls + 1}"
    if board is not None:
        board.create_adhoc(
            task_key=task_key,
            role="research.orchestrator",
            brief={"goal": f"协调员轻量核验：{tool_name}", "channel_hint": "web", "output_format": "evidence"},
            owner_agent="research.orchestrator",
        )
    adapter = McpClientAdapter(server=default_mcp_server)
    arguments = _arguments(state)
    outcome: ToolOutcome | None = None
    gaps: List[str] = []
    try:
        enforce_tool_allowed(tool_name, scope, caller=ToolCaller.ORCHESTRATOR)
        if tool_name != "get_current_time" and not str(state.get("query_or_url") or "").strip():
            raise Denied("协调员工具缺少查询词或网址", "INVALID_TOOL_ARGUMENTS", 400)
        emit_progress(hooks, f"[tool_execution_start] [调研协调员] 直接调用工具 {tool_name}")
        outcome = adapter.call_tool(
            tool_name,
            arguments,
            remaining_seconds=max(1.0, float(state.get("timeout_seconds") or 450.0)),
            cancel_event=hooks.cancel_event,
        )
        if outcome.status in ("error", "timeout", "cancelled"):
            gaps.append(f"{tool_name}({outcome.status}): {outcome.error_reason or '无匹配数据'}")
        elif outcome.status == "empty":
            gaps.append(f"{tool_name}: 未返回匹配结果。")
    except Denied as exc:
        gaps.append(exc.message)
        record_diagnostic("permission.denied" if exc.status == 403 else "tool.error", "failed", exc)
    except Exception as exc:
        gaps.append(f"{tool_name or '协调工具'} 调用失败：{type(exc).__name__}")
        record_diagnostic("tool.error", "failed", exc)

    elapsed = round(time.perf_counter() - started, 2)
    channel = "clock" if tool_name == "get_current_time" else "web"
    platform_items: Dict[str, List[Dict[str, Any]]] = {}
    time_references: List[str] = []
    data_count = 0
    status = "error"
    summary = "；".join(gaps) or "协调员工具未返回结果"
    if outcome is not None:
        data_count = outcome.data_count
        status = outcome.status
        summary = (
            f"{tool_name} 返回 {outcome.data_count} 条结果"
            if outcome.status == "ok"
            else f"{tool_name} 状态={outcome.status}: {outcome.error_reason or '无匹配数据'}"
        )
        if tool_name == "get_current_time" and outcome.items:
            reading = str(outcome.items[0].get("current_time") or "")
            if reading:
                time_references.append(f"{reading} ({outcome.items[0].get('timezone', '')})")
        elif outcome.items:
            platform_items = {"web": [item for item in outcome.items if isinstance(item, dict)][:10]}

    evidence: EvidenceRecord = {
        "evidence_id": f"coord_{state.get('job_id') or 'task'}_{current_calls + 1}",
        "agent_id": "research.orchestrator",
        "role": "research.orchestrator",
        "channel": channel,
        "goal": f"协调员轻量核验：{tool_name}",
        "status": status if outcome is not None else "error",
        "summary": AgentSession.after_tool_sanitize(summary)[0],
        "key_metrics": [],
        "gaps_or_errors": gaps,
        "platform_items": platform_items,
        "time_references": time_references,
        "tool_counts": {tool_name: 1} if tool_name else {},
        "elapsed": elapsed,
    }
    transition: AuditableAgentTransition = {
        "agent_id": "research.orchestrator",
        "role": "research.orchestrator",
        "task_id": f"coordinator_{current_calls + 1}",
        "agent_type": "coordinator",
        "state_from": AgentRunState.COORDINATING.value,
        "state_to": (
            AgentRunState.COMPLETED.value
            if status != "error"
            else AgentRunState.PARTIAL_OR_FAILED.value
        ),
        "decision_reason": str(state.get("decision_reason") or "协调员轻量核验"),
        "tools": [
            {
                "tool_name": tool_name or "unknown",
                "parameters": arguments,
                "dispatched": bool(outcome.dispatched) if outcome is not None else False,
                "status": status,
                "result_summary": summary,
                "data_count": data_count,
                "elapsed": elapsed,
            }
        ],
        "elapsed": elapsed,
        "scratch_files": [],
    }
    emit_progress(hooks, f"[tool_execution_end] [调研协调员] {tool_name or '工具'} 核验完成 · {summary}")
    if board is not None:
        board_result = {
            "task_id": task_key,
            "role": "research.orchestrator",
            "status": evidence["status"],
            "summary": evidence["summary"],
            "evidence_ids": [evidence["evidence_id"]],
            "tool_records": transition["tools"],
            "gaps": gaps,
            "elapsed": elapsed,
        }
        if evidence["status"] == "error":
            board.fail(task_key, "；".join(gaps) or "coordinator tool failed", board_result)
        else:
            board.complete(task_key, board_result)
    return {
        "evidence_pool": [evidence],
        "audit_trail": [transition],
        "coordinator_tool_calls": current_calls + 1,
        "system_status": ResearchStatus.RUNNING.value,
        "stop_reason": StopReason.CONTINUE.value,
        "cancelled": False,
    }