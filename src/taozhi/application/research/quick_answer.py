"""Supervisor 派发的速答 Worker：一次问答，最多一次工具调用，不生成完整研报。"""
from __future__ import annotations

import json
import time
from contextlib import closing
from typing import Any, Dict, List, Tuple

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.core.agent.session import AgentSession
from taozhi.core.tools.outcome import ToolOutcome
from taozhi.domain.agent_catalog import agent_display_name
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
from taozhi.domain.text_guard import sanitize
from taozhi.infrastructure.mcp.adapter import McpClientAdapter
from taozhi.infrastructure.mcp.server import default_mcp_server
from taozhi.observability.tracing import record_diagnostic
from .hooks import (
    attach_route_delta,
    emit_progress,
    get_hooks,
    is_cancelled,
    make_failover_emitter,
)
from .prompts import RESEARCH_PROMPTS
from .task_board import current_task_board
from .schemas import (
    AutonomousAgentState,
    AuditableAgentTransition,
    EvidenceRecord,
    _QuickAnswerSchema,
)

_INSUFFICIENT = "当前没有足够的授权资料，暂时无法确认这一问题的结论。"


def _tool_arguments(decision: _QuickAnswerSchema) -> Dict[str, Any]:
    """把速答决策转换为 MCP 工具参数。"""
    if decision.tool_name == "get_current_time":
        return {"timezone": decision.timezone} if decision.timezone is not None else {}
    if decision.tool_name == "fetch_web_page":
        return {"url": decision.query_or_url.strip()}
    if decision.tool_name == "search_1688_wholesale":
        return {"keyword": decision.query_or_url.strip()}
    return {"query": decision.query_or_url.strip()}


def _build_quick_prompt(query: str, keyword: str, specs: List[Dict[str, Any]]) -> Tuple[str, str]:
    """渲染速答 Worker 的决策提示词。"""
    system, user = RESEARCH_PROMPTS.render(
        "research.quick.decision",
        {
            "query": sanitize(query),
            "keyword": sanitize(keyword),
            "specs": json.dumps(specs, ensure_ascii=False),
        },
    )
    return system or "", user or ""


def _answer_from_observation(
    *,
    llm: LLMClient,
    query: str,
    keyword: str,
    tool_name: str,
    observation: str,
    gaps: List[str],
) -> str:
    """在工具观测后生成最终简洁回答；失败时返回空串由调用方降级。"""
    system, prompt = RESEARCH_PROMPTS.render(
        "research.quick.answer",
        {
            "query": sanitize(query),
            "keyword": sanitize(keyword),
            "tool_name": tool_name,
            "observation": observation,
        },
    )
    try:
        return llm.chat(
            prompt or "",
            tier=ModelTier.FAST,
            system=system or "",
            max_tokens=800,
        ).strip()
    except Exception as exc:
        record_diagnostic("workflow.summary", "fallback", exc)
        gaps.append("速答模型暂时不可用。")
        return ""


def _tool_record(outcome: ToolOutcome, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """构造单次工具调用的审计记录。"""
    if outcome.status != "ok":
        summary = f"状态={outcome.status}: {outcome.error_reason or '无匹配数据'}"
    else:
        summary = f"提取 {outcome.data_count} 条结果 (耗时 {outcome.elapsed}s)"
    return {
        "tool_name": outcome.tool_name,
        "parameters": arguments,
        "dispatched": outcome.dispatched,
        "status": outcome.status,
        "result_summary": summary,
        "data_count": outcome.data_count,
        "elapsed": outcome.elapsed,
    }


def quick_answer_worker_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """速答 Worker：一次问答，最多一次工具调用，直接给出简洁回答并回写证据。"""
    hooks = get_hooks(state.get("hook_run_id", ""))
    query = str(state.get("user_query") or state.get("keyword") or "")
    keyword = str(state.get("keyword") or "")
    steering = list(state.get("steering_inbox") or [])
    started_at = float(state.get("started_at") or time.time())
    timeout_seconds = float(state.get("timeout_seconds") or 450.0)
    elapsed_at_start = max(0.0, time.time() - started_at)
    remaining = max(1.0, timeout_seconds - elapsed_at_start)

    if is_cancelled(hooks, bool(state.get("cancelled", False))):
        return {
            "cancelled": True,
            "report": "",
            "system_status": ResearchStatus.CANCELLED.value,
            "stop_reason": StopReason.CANCELLED.value,
        }

    scope = classify_research_scope(query, steering)
    board = current_task_board()
    if board is not None:
        board.create_adhoc(
            task_key="quick_answer",
            role="research.quick_answer",
            brief={"goal": query, "channel_hint": "direct", "output_format": "text"},
            owner_agent="research.quick_answer",
        )
    adapter = McpClientAdapter(server=default_mcp_server)
    specs = adapter.list_tool_specs(tools_for_caller(ToolCaller.QUICK_ANSWER, scope))
    emit_progress(hooks, f"[agent_start] 启动「{agent_display_name('research.quick_answer')}」· 直接回答一次提问")

    tools_records: List[Dict[str, Any]] = []
    gaps: List[str] = []
    platform_items: Dict[str, List[Dict[str, Any]]] = {}
    time_references: List[str] = []
    channel = "direct"
    evidence_status = "empty"
    answer = ""
    decision: _QuickAnswerSchema
    route_events: List[Dict[str, Any]] = []
    last_endpoint = ""
    tool_started = time.perf_counter()

    with closing(LLMClient(on_attempt=hooks.model_call_observer("research.quick_answer"))) as llm, route_audit_scope(
        preferred_endpoint_id=str(state.get("active_llm_endpoint") or "") or None,
        on_failover=make_failover_emitter(hooks),
    ) as route_recorder:
        try:
            system, prompt = _build_quick_prompt(query, keyword, specs)
            raw = llm.chat_json(
                prompt=prompt,
                schema=_QuickAnswerSchema,
                tier=ModelTier.FAST,
                system=system,
                retries=2,
            )
            decision = (
                raw
                if isinstance(raw, _QuickAnswerSchema)
                else _QuickAnswerSchema.model_validate(raw)
            )
        except Exception as exc:
            record_diagnostic("workflow.plan", "fallback", exc)
            decision = _QuickAnswerSchema(action="answer", thought="速答模型不可用", answer="", gaps=["速答模型暂时不可用。"])

        if decision.action == "answer":
            answer = decision.answer.strip()
            gaps.extend(str(item) for item in decision.gaps if item)
            evidence_status = "ok" if answer else "empty"
        else:
            tool_name = decision.tool_name.strip()
            query_or_url = decision.query_or_url.strip()
            try:
                if not query_or_url and tool_name != "get_current_time":
                    raise Denied("速答工具缺少查询词或网址", "INVALID_TOOL_ARGUMENTS", 400)
                enforce_tool_allowed(tool_name, scope, caller=ToolCaller.QUICK_ANSWER)
                arguments = _tool_arguments(decision)
                outcome = adapter.call_tool(
                    tool_name,
                    arguments,
                    remaining_seconds=remaining,
                    cancel_event=hooks.cancel_event,
                )
                tools_records.append(_tool_record(outcome, arguments))
                if tool_name == "get_current_time":
                    channel = "clock"
                    if outcome.items:
                        reading = str(outcome.items[0].get("current_time") or "")
                        if reading:
                            time_references.append(f"{reading} ({outcome.items[0].get('timezone', '')})")
                elif outcome.items:
                    channel = "web"
                    platform_items = {"web": [item for item in outcome.items if isinstance(item, dict)][:10]}
                if outcome.status in ("error", "timeout", "cancelled"):
                    gaps.append(f"{tool_name}({outcome.status}): {outcome.error_reason or '无匹配数据'}")
                elif outcome.status == "empty":
                    gaps.append(f"{tool_name}: 未返回匹配结果。")
                text_preview, injection_hits = AgentSession.after_tool_sanitize(outcome.text_preview)
                if injection_hits:
                    gaps.append(f"{tool_name} 返回内容触发外部注入警告（{','.join(injection_hits)}）")
                observation = json.dumps(
                    {
                        "tool": tool_name,
                        "status": outcome.status,
                        "data_count": outcome.data_count,
                        "preview": text_preview,
                    },
                    ensure_ascii=False,
                )
                if outcome.status in ("error", "timeout", "cancelled"):
                    answer = ""
                else:
                    answer = _answer_from_observation(
                        llm=llm,
                        query=query,
                        keyword=keyword,
                        tool_name=tool_name,
                        observation=observation,
                        gaps=gaps,
                    )
                evidence_status = (
                    "ok"
                    if outcome.status == "ok" and outcome.data_count > 0 and answer
                    else ("error" if outcome.status in ("error", "timeout", "cancelled") else "empty")
                )
            except Denied as exc:
                gaps.append(exc.message)
                record_diagnostic("permission.denied" if exc.status == 403 else "tool.error", "failed", exc)
                evidence_status = "error"
            except Exception as exc:
                gaps.append(f"{tool_name or '速答工具'} 调用失败：{type(exc).__name__}")
                record_diagnostic("tool.error", "failed", exc)
                evidence_status = "error"

        route_events = route_recorder.snapshot()
        last_endpoint = route_recorder.last_active_endpoint

    if not answer:
        answer = _INSUFFICIENT + (f"\n\n缺口：{'；'.join(gaps)}" if gaps else "")

    elapsed = round(time.perf_counter() - tool_started, 2)
    tool_counts: Dict[str, int] = {}
    for record in tools_records:
        tool_counts[record["tool_name"]] = tool_counts.get(record["tool_name"], 0) + 1

    evidence: EvidenceRecord = {
        "evidence_id": f"quick_{state.get('job_id') or 'task'}",
        "agent_id": "research.quick_answer",
        "role": "research.quick_answer",
        "channel": channel,
        "goal": query,
        "status": evidence_status,
        "summary": AgentSession.after_tool_sanitize(answer)[0],
        "key_metrics": [],
        "gaps_or_errors": gaps,
        "platform_items": platform_items,
        "time_references": time_references,
        "tool_counts": tool_counts,
        "elapsed": elapsed,
    }
    transition: AuditableAgentTransition = {
        "agent_id": "research.quick_answer",
        "role": "research.quick_answer",
        "task_id": "quick_answer",
        "agent_type": "quick_answer",
        "state_from": AgentRunState.ANSWERING.value,
        "state_to": (
            AgentRunState.COMPLETED.value
            if evidence_status != "error"
            else AgentRunState.PARTIAL_OR_FAILED.value
        ),
        "decision_reason": decision.thought or "速答专员直接回答",
        "tools": tools_records,
        "elapsed": elapsed,
        "scratch_files": [],
    }
    emit_progress(hooks, f"[agent_end] 「{agent_display_name('research.quick_answer')}」完成一次问答 · 状态：{evidence_status}")

    if board is not None:
        board_result = {
            "task_id": "quick_answer",
            "role": "research.quick_answer",
            "status": evidence_status,
            "summary": evidence["summary"],
            "evidence_ids": [evidence["evidence_id"]],
            "tool_records": tools_records,
            "gaps": gaps,
            "elapsed": elapsed,
        }
        if evidence_status == "error":
            board.fail("quick_answer", "；".join(gaps) or "quick answer failed", board_result)
        else:
            board.complete("quick_answer", board_result)

    result = {
        "report": answer,
        "outline": [],
        "evidence_pool": [evidence],
        "audit_trail": [transition],
        "decision_reason": decision.thought or "速答专员直接回答",
        "system_status": ResearchStatus.COMPLETED.value,
        "stop_reason": StopReason.COMPLETED.value,
        "cancelled": False,
    }
    return attach_route_delta(result, route_events, last_endpoint)