"""终审研报合成专家与长效知识库沉淀节点。"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

_LOG = logging.getLogger(__name__)

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.router import route_audit_scope
from taozhi.core.agent.context import ArtifactMetadata, SubagentContext
from taozhi.core.memory.service import get_memory_store
from taozhi.domain.research import StopReason
from .hooks import attach_route_delta, emit_progress, get_hooks, make_failover_emitter
from .schemas import AuditableAgentTransition, AutonomousAgentState, active_evidence


def synthesizer_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终审研报合成专家（ModelTier.STRONG）：仅引用黑板实证生成带缺口声明的 Markdown 研报。"""
    t0 = time.perf_counter()
    hooks = get_hooks(state.get("hook_run_id", ""))
    keyword = state.get("keyword") or state.get("user_query") or ""
    user_query = state.get("user_query") or keyword
    outline = state.get("outline") or ["市场概况与用户痛点", "竞品价格带格局", "落地决策建议"]
    inbox = state.get("steering_inbox") or []
    active_ev = active_evidence(state)

    emit_progress(hooks, "[agent_handoff] 角色交接: lead_agent ➔ 终审研报合成专家 · 整合黑板有效证据", 85)
    ctx = SubagentContext(conversation_id=f"rep_{state.get('job_id', 'task')}_{int(time.time())}", role="终审研报合成专家")

    evidence_payload = [
        {
            "evidence_id": ev.get("evidence_id"),
            "role": ev.get("role"),
            "channel": ev.get("channel"),
            "status": ev.get("status"),
            "goal": ev.get("goal"),
            "summary": ev.get("summary"),
            "key_metrics": ev.get("key_metrics"),
            "gaps_or_errors": ev.get("gaps_or_errors"),
            "tool_counts": ev.get("tool_counts"),
        }
        for ev in active_ev
    ]

    steer_block = f"\n【运行期动态干预要求（必须显式回应落地）】：{'；'.join(inbox)}\n" if inbox else ""
    prompt = (
        f"目标品类：【{keyword}】\n"
        f"用户完整诉求：{user_query}\n"
        f"规划大纲章节：{'；'.join(outline)}\n"
        f"{steer_block}\n"
        f"【黑板已回收有效证据包（共 {len(evidence_payload)} 份）】：\n"
        f"{json.dumps(evidence_payload, ensure_ascii=False)}\n\n"
        "请综合上述多智能体真实采回的证据撰写结构化、易读的 Markdown 市场调研报告。\n"
        "【排版与可读性要求】：\n"
        "1. 结论先行：开篇先给出「## 核心结论速览」，提炼关键价格带、核心需求点与首推策略，方便快速浏览；\n"
        "2. 表格对比：涉及竞品、价格区间、1688 源头报价与 MOQ 等多维对比时，优先使用标准 Markdown 表格呈现；\n"
        "3. 重点分明：用简短分点与加粗突出核心指标，数据缺口或渠道异常请用 `> ⚠️ 数据缺口与风险提示：` 引用块独立标出。\n"
        "【严格准则】：\n"
        "1. 一切价格、起订量、销量、差评与工厂/商品线索必须严格来自上述黑板证据，绝不允许编造未出现的数字；\n"
        "2. 若某渠道 status 为 empty/error 或 gaps_or_errors 非空，必须在报告对应章节客观说明数据缺失原因与可替代方案，严禁粉饰或凭空补齐；\n"
        "3. 结合用户诉求给出可落地的差异化与风险提示。"
    )

    llm = LLMClient()
    emit_progress(hooks, "[tool_execution_start] [终审研报合成专家] 调度工具: generate_markdown_report", 90)
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
                system="你是资深电商操盘负责人。输出客观、可核验的高质量 Markdown 研报，绝对禁止虚构数据。",
            )
        except Exception as exc:
            sections = [f"### {ev.get('role')}（{ev.get('status')}）\n{ev.get('summary', '')}" for ev in active_ev]
            body = "\n\n".join(sections) if sections else "本次暂无可用实测证据。"
            final_report = (
                f"# 【{keyword}】电商市场调研报告（降级结构化汇总）\n\n"
                f"> 注：终审大模型生成阶段发生异常（{type(exc).__name__}），以下内容严格由各子智能体实测证据直接汇总，未作二次推演。\n\n"
                f"{body}"
            )
        synth_route_events = synth_rec.snapshot()
        synth_last_ep = synth_rec.last_active_endpoint

    art_meta = ArtifactMetadata(summary=f"【{keyword}】电商多智能体调研报告", user_facing=True)
    art_file = ctx.write_artifact("market_research_report.md", final_report, metadata=art_meta)
    elapsed = round(time.perf_counter() - t0, 2)
    emit_progress(hooks, f"[tool_execution_end] [终审研报合成专家] 研报生成完毕 · {len(final_report)} 字符 (耗时 {elapsed}s)", 95)

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "终审研报合成专家",
        "type_name": "synthesizer_agent",
        "state_from": "SYNTHESIZING",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": [
            {
                "tool_name": "generate_markdown_report",
                "parameters": {"evidence_count": len(active_ev), "outline_count": len(outline)},
                "status": "ok",
                "result_summary": f"基于 {len(active_ev)} 份黑板实证生成终审 Markdown 研报（{len(final_report)} 字符）。",
                "data_count": len(active_ev),
                "elapsed": elapsed,
            }
        ],
        "elapsed": elapsed,
        "scratch_files": [],
    }
    return attach_route_delta(
        {
            "report": final_report,
            "artifacts": [str(art_file)],
            "audit_trail": [transition],
            "system_status": "COMPLETED" if state.get("stop_reason") == StopReason.CONTINUE.value else state.get("system_status", "COMPLETED"),
        },
        synth_route_events,
        synth_last_ep,
    )


def final_persistence_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终态沉淀节点：仅在非取消状态下将大纲与实测摘要写入租户长效知识库。"""
    if bool(state.get("cancelled", False)):
        return {"system_status": "CANCELLED"}

    keyword = state.get("keyword") or ""
    tenant_id = state.get("tenant_id") or "default"
    report = state.get("report") or ""
    outline = state.get("outline") or []
    try:
        mem = get_memory_store(tenant_id=tenant_id)
        if keyword and outline:
            mem.remember_fact(subject=keyword, predicate="全景调研大纲", object_val="；".join(outline), confidence=1.0)
        if keyword and report:
            mem.remember_fact(subject=keyword, predicate="全景调研报告摘要", object_val=report[:250], confidence=0.95)
    except Exception as exc:
        _LOG.warning("沉淀终态调研记忆事实失败 (%s): %s", keyword, exc)

    final_status = "TIMEOUT" if state.get("stop_reason") == StopReason.TIMEOUT.value and not report else "COMPLETED"
    return {"system_status": final_status}
