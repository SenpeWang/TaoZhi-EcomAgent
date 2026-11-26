"""终审研报合成专家与长效知识库沉淀节点。"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any, Dict, List


from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.ai.text_stream import OutputDeliveryError
from taozhi.core.agent.context import AgentContext, ArtifactMetadata
from taozhi.core.memory.service import get_memory_store
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.research import AgentRunState, ResearchStatus, StopReason
from taozhi.observability.tracing import node_span, record_diagnostic
from .hooks import attach_route_delta, emit_progress, get_hooks, make_failover_emitter
from .prompts import RESEARCH_PROMPTS
from .task_board import current_task_board
from .schemas import AuditableAgentTransition, AutonomousAgentState, EvidenceRecord, active_evidence


def _evidence_report(keyword: str, evidence: list[EvidenceRecord], error_type: str) -> Iterator[str]:
    """按现有证据逐节生成降级报告，供实时交付与归档共用。"""
    yield (
        f"# 【{keyword}】电商市场调研报告（降级结构化汇总）\n\n"
        f"> 注：终审大模型生成阶段发生异常（{error_type}），以下内容严格由各子智能体实测证据直接汇总，未作二次推演。"
    )
    if not evidence:
        yield "\n\n本次暂无可用实测证据。"
    for ev in evidence:
        yield f"\n\n### {agent_display_name(ev['role'])}（{ev.get('status')}）\n{ev.get('summary', '')}"
        if ev.get("channel") != "clock" and ev.get("time_references"):
            yield "\n\n时间参照：" + "、".join(ev["time_references"])


def synthesizer_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终审研报合成专家（ModelTier.STRONG）：仅引用黑板实证生成带缺口声明的 Markdown 研报。"""
    t0 = time.perf_counter()
    hooks = get_hooks(state.get("hook_run_id", ""))
    keyword = state.get("keyword") or state.get("user_query") or ""
    user_query = state.get("user_query") or keyword
    outline = state.get("outline") or ["市场概况与用户痛点", "竞品价格带格局", "落地决策建议"]
    inbox = state.get("steering_inbox") or []
    active_ev = active_evidence(state)

    emit_progress(hooks, "[agent_handoff] 整合已核验黑板证据，转入终审研报合成", 85)
    ctx = AgentContext(conversation_id=f"rep_{state.get('job_id', 'task')}_{int(time.time())}", role=agent_display_name("research.synthesizer"))

    with node_span("context.prepare_report", "CHAIN", {"context.evidence_count": len(active_ev)}) as span:
        evidence_payload = [
            {
                "evidence_id": ev.get("evidence_id"),
                "role": agent_display_name(ev["role"]),
                "channel": ev.get("channel"),
                "status": ev.get("status"),
                "goal": ev.get("goal"),
                "summary": ev.get("summary"),
                "key_metrics": ev.get("key_metrics"),
                "gaps_or_errors": ev.get("gaps_or_errors"),
                "tool_counts": ev.get("tool_counts"),
                "time_references": ev.get("time_references", []),
            }
            for ev in active_ev
        ]

        steer_block = f"\n【运行期动态干预要求（必须显式回应落地）】：{'；'.join(inbox)}\n" if inbox else ""
        system, prompt = RESEARCH_PROMPTS.render(
            "research.synthesizer.report",
            {
                "keyword": keyword,
                "user_query": user_query,
                "outline_joined": "；".join(outline),
                "steer_block": steer_block,
                "evidence_count": len(evidence_payload),
                "evidence_json": json.dumps(evidence_payload, ensure_ascii=False),
            },
        )
        if span is not None:
            span.set_attribute("context.output_chars", len(prompt))

    llm = LLMClient(on_attempt=hooks.model_call_observer("research.synthesizer"))
    emit_progress(hooks, "[agent_start] 调研报告分析师开始撰写报告", 90)
    synth_route_events: List[Dict[str, Any]] = []
    synth_last_ep = ""
    with route_audit_scope(
        preferred_endpoint_id=str(state.get("active_llm_endpoint") or "") or None,
        on_failover=make_failover_emitter(hooks),
    ) as synth_rec:
        try:
            final_report = llm.chat(
                prompt,
                tier=ModelTier.STRONG,
                system=system or "",
                on_delta=hooks.result_delta_fn,
                on_reset=hooks.result_reset_fn,
            )
            if not final_report:
                raise ValueError("模型未返回报告正文")
        except OutputDeliveryError:
            raise
        except Exception as exc:
            record_diagnostic("workflow.report", "fallback", exc)
            parts = []
            for section in _evidence_report(keyword, active_ev, type(exc).__name__):
                parts.append(section)
                if hooks.result_delta_fn:
                    hooks.result_delta_fn(section)
            final_report = "".join(parts)
        finally:
            llm.close()
        synth_route_events = synth_rec.snapshot()
        synth_last_ep = synth_rec.last_active_endpoint

    art_meta = ArtifactMetadata(summary=f"【{keyword}】电商多智能体调研报告", user_facing=True)
    art_file = ctx.write_artifact("market_research_report.md", final_report, metadata=art_meta)
    elapsed = round(time.perf_counter() - t0, 2)
    emit_progress(hooks, f"[agent_end] 调研报告分析师完成报告 · {len(final_report)} 字符（耗时 {elapsed}s）", 95)

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "research.synthesizer",
        "task_id": "",
        "agent_type": "synthesizer",
        "state_from": AgentRunState.SYNTHESIZING.value,
        "state_to": AgentRunState.COMPLETED.value,
        "decision_reason": state.get("decision_reason", ""),
        "tools": [],
        "elapsed": elapsed,
        "scratch_files": [],
    }
    return attach_route_delta(
        {
            "report": final_report,
            "artifacts": [str(art_file)],
            "audit_trail": [transition],
            "system_status": ResearchStatus.COMPLETED.value if state.get("stop_reason") == StopReason.CONTINUE.value else state.get("system_status", ResearchStatus.COMPLETED.value),
        },
        synth_route_events,
        synth_last_ep,
    )


def archive_research(state: AutonomousAgentState) -> Dict[str, Any]:
    """终态沉淀节点：仅在非取消状态下将大纲与实测摘要写入租户长效知识库。"""
    board = current_task_board()
    if board is not None:
        board.cancel_incomplete()
    if bool(state.get("cancelled", False)):
        return {"system_status": ResearchStatus.CANCELLED.value}

    keyword = state.get("keyword") or ""
    tenant_id = state.get("tenant_id") or "default"
    report = state.get("report") or ""
    final_status = ResearchStatus.TIMEOUT.value if state.get("stop_reason") == StopReason.TIMEOUT.value and not report else ResearchStatus.COMPLETED.value
    evidence = active_evidence(state)
    if evidence and all(ev.get("channel") == "clock" for ev in evidence):
        return {"system_status": final_status}
    outline = state.get("outline") or []
    try:
        mem = get_memory_store(tenant_id=tenant_id)
        if keyword and outline:
            mem.remember_fact(subject=keyword, predicate="全景调研大纲", object_val="；".join(outline), confidence=1.0)
        if keyword and report:
            mem.remember_fact(subject=keyword, predicate="全景调研报告摘要", object_val=report[:250], confidence=0.95)
    except Exception as exc:
        record_diagnostic("storage.memory", "skipped", exc)

    return {"system_status": final_status}
