"""⑥ 答案生成 Agent：商品问答答案卡 + 强制引用溯源（PRD 模块 F）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ..context import collapse
from ..llm import ModelTier, get_llm
from ..llm.json_utils import extract_json
from ..observability import inc, log_event
from ..safety.rejection import build_refusal, log_rejection, should_refuse, to_text
from ..schemas.common import Citation, Evidence, PermissionContext
from ..schemas.research import (
    DebateMatrix,
    KeyProduct,
    ReportSection,
    ResearchReport,
    ResearchTask,
)
from .state import ResearchRuntime, ResearchState, append_trace
from .specialists import trusted_evidence

_REPORT_PROMPT = """你是电商商品知识问答的【答案生成主笔】。请基于证据链生成结构化的商品问答答案。

用户问题：{question}
关注产品线：{focus}
【会话历史】（用于「它 / 上面那款」等指代消解；为「（无）」表示本轮是会话首轮）
{history}
商品图谱要点：{graph}
交叉核验结论：{hypotheses}
口径辩论裁决：{debate}

可引用证据（编号即引用序号，务必只使用这些编号）：
{evidence}

写作要求（直接输出 JSON，不要任何前言、标题或解释，不要输出思考过程）：
1. sections 按问题类型依次选用以下板块（与问题无关的板块省略）：
   结论先行答案卡 → 参数对比表（型号对比时）→ 适配结论与替代方案（适配推荐时）
   → 操作步骤（使用教程时）→ 常见故障排查（售后问答时）→ 引用溯源列表；
2. 每个板块 content 150-350 字，出现的论断必须带引用标记（标注来源文档与段落），
   如"……（[0][3]）"；citation_indices 填该板块用到的证据编号数组；
3. executive_summary 为 3-4 句话的结论先行答案卡，直接回答用户问题并覆盖关键参数；
4. key_products 给出 3-6 个重点商品型号（name=商品名称 / model=型号 /
   highlights=核心卖点 / citation_indices）；
5. risk_warnings 与 recommendations 各 3 条，每条不超过 40 字（口径分歧提示 / 选购与保养建议）；
6. 禁止使用证据之外的参数数字与事实；不确定处写明"待核实"。

输出 JSON：
{{"title":"","executive_summary":"",
"sections":[{{"heading":"","content":"","citation_indices":[0]}}],
"key_products":[{{"name":"","model":"","highlights":"","citation_indices":[0]}}],
"risk_warnings":[""],"recommendations":[""]}}
"""


def report_generation_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    task: ResearchTask = state["task"]
    started = time.perf_counter()

    state["evidence_pool"] = trusted_evidence(state)
    # ── 拒答门禁（Q12）：证据为空或整体置信度低于阈值时不编造，直接结构化拒答 ──
    reason = _gate_refusal(state)
    if reason:
        return _refusal_state(state, task, reason, started)

    evidence_pool: List[Evidence] = list(state.get("evidence_pool", []))[:24]
    citations = _build_citations(evidence_pool)

    report = _generate_report(state, task, evidence_pool, citations)
    report = _enforce_citations(report, citations)
    report.task_id = task.task_id
    report.question = task.question
    report.entities = list(state.get("extraction").entities) if state.get("extraction") else []
    report.triples = list(state.get("extraction").triples) if state.get("extraction") else []
    report.chain_analysis = {"scope": "authorized_evidence_only", "business": state.get("business", {})}
    report.hypotheses_summary = runtime.tracker.summary_rows() if runtime.tracker else []
    report.debate_matrix = state.get("debate")
    report.citations = citations
    report.confidence = _overall_confidence(state)
    report.citation_coverage = _coverage(report)
    report.review_required = (
        report.citation_coverage < runtime.settings.citation_coverage_target
        or report.confidence < runtime.settings.confidence_review_threshold
        or any(h.needs_human_review for h in state.get("hypotheses", []))
    )
    report.model_used = runtime.settings.llm_strong_model
    report.elapsed_seconds = round(time.perf_counter() - started, 2)

    inc("report.generated")
    log_event("report_generated", task_id=task.task_id,
              coverage=report.citation_coverage, confidence=report.confidence)
    append_trace(state, "report_generation",
                 f"生成答案：{len(report.sections)} 章节 / {len(citations)} 条引用，"
                 f"溯源覆盖率 {report.citation_coverage:.0%}",
                 report.elapsed_seconds)

    return {
        "report": report,
        "citations": citations,
        "trace": state.get("trace", []),
        "metrics": {**state.get("metrics", {}),
                    "citation_coverage": report.citation_coverage,
                    "report_confidence": report.confidence},
    }


# ───────────────────────── 内部实现 ─────────────────────────


def _gate_refusal(state: ResearchState) -> str:
    """拒答判定：①证据条数为 0 ②整体置信度 < REJECT_CONFIDENCE_THRESHOLD。

    整体置信度取 deep_research 链路产出的核验假设置信度均值（_overall_confidence）。
    返回拒答原因；不拒答返回空串。
    """
    evidence_count = len(state.get("evidence_pool") or [])
    confidence = _overall_confidence(state) if evidence_count else None
    refuse, reason = should_refuse(evidence_count, confidence)
    return reason if refuse else ""


def _refusal_state(state: ResearchState, task: ResearchTask,
                   reason: str, started: float) -> Dict[str, Any]:
    """构造报告形态的结构化拒答（不调用 LLM），并写拒答审计 rejections.jsonl。"""
    refusal = build_refusal(task.question, reason)
    refusal_text = to_text(refusal)
    log_rejection(task.question, reason, task_id=task.task_id)
    report = ResearchReport(
        title="暂时无法回答",
        question=task.question,
        executive_summary=refusal["message"],
        sections=[ReportSection(heading="无法回答说明", content=refusal_text,
                                citation_indices=[])],
        risk_warnings=[refusal["missing_info"]],
        recommendations=[refusal["escalate"], "补充相关商品资料（参数表 / 手册 / FAQ）后可重新提问"],
        confidence=0.0,
        citation_coverage=0.0,
        review_required=False,
        model_used="rule-gate",
        elapsed_seconds=round(time.perf_counter() - started, 2),
    )
    append_trace(state, "report_generation",
                 f"拒答门禁触发（{reason}）：不生成答案，输出转人工引导",
                 report.elapsed_seconds)
    inc("report.refused")
    log_event("report_refused", task_id=task.task_id, reason=reason)
    return {
        "report": report,
        "citations": [],
        "trace": state.get("trace", []),
        "metrics": {**state.get("metrics", {}), "refusal_reason": reason},
    }


def _build_citations(pool: List[Evidence]) -> List[Citation]:
    citations: List[Citation] = []
    seen = set()
    for item in pool:
        key = (item.doc_name, item.quote[:60])
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            Citation(
                index=len(citations),
                doc_name=item.doc_name or item.source_id,
                page=item.page,
                quote=item.quote[:240],
                url=item.url,
                source=item.doc_type.value if hasattr(item.doc_type, "value") else str(item.doc_type),
            )
        )
    return citations


def _generate_report(state: ResearchState, task: ResearchTask,
                     pool: List[Evidence], citations: List[Citation]) -> ResearchReport:
    if not pool:
        return _fallback_report(task, state=state)
    if not get_llm().settings.has_llm:
        return _fallback_report(task, pool, citations, state)

    graph_brief = "仅允许使用下面已授权的原文证据；全局图谱不作为答复依据"
    hypotheses_brief = _hypotheses_brief(state)
    debate_brief = _debate_brief(state.get("debate"))
    # 注入会话历史（【会话历史】文本块），供「它 / 上面那款」等指代消解
    history = str(state.get("session_history", "") or "").strip() or "（无）"
    prompt = _REPORT_PROMPT.format(
        question=task.question,
        focus=task.product_line or task.category or "全产品线",
        history=history[:2000],
        graph=graph_brief[:1500],
        hypotheses=hypotheses_brief[:1200] + "\n专家核验："+str(state.get("specialists", []))[:2000],
        debate=debate_brief[:600],
        evidence="\n".join(f"[{c.index}] {c.doc_name}: {c.quote}" for c in citations),
    )
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.STRONG, max_tokens=6000)
        data = extract_json(raw, expect="object") or {}
    except Exception:  # noqa: BLE001
        data = {}

    sections = [
        ReportSection(
            heading=str(s.get("heading", "")).strip() or f"章节 {i + 1}",
            content=str(s.get("content", "")).strip(),
            citation_indices=[int(x) for x in (s.get("citation_indices") or []) if _is_int(x)][:8],
        )
        for i, s in enumerate(data.get("sections") or [])
        if isinstance(s, dict) and s.get("content")
    ]
    products = [
        KeyProduct(
            name=str(c.get("name", "")).strip(),
            model=str(c.get("model", "")).strip(),
            highlights=str(c.get("highlights", "")).strip(),
            citation_indices=[int(x) for x in (c.get("citation_indices") or []) if _is_int(x)][:6],
        )
        for c in (data.get("key_products") or data.get("key_companies") or [])
        if isinstance(c, dict) and c.get("name")
    ]
    report = ResearchReport(
        title=str(data.get("title", "")).strip() or f"{task.question}——商品知识问答报告",
        executive_summary=str(data.get("executive_summary", "")).strip(),
        sections=sections,
        key_products=products,
        risk_warnings=[str(x) for x in (data.get("risk_warnings") or [])][:6],
        recommendations=[str(x) for x in (data.get("recommendations") or [])][:6],
    )
    if not report.sections:
        return _fallback_report(task, pool, citations, state)
    return report


def _enforce_citations(report: ResearchReport, citations: List[Citation]) -> ResearchReport:
    """Remove invalid indices; never attach an arbitrary citation to a claim."""
    valid_indices = {c.index for c in citations}
    for section in report.sections:
        section.citation_indices = [i for i in section.citation_indices if i in valid_indices]
    return report


def _fallback_report(task: ResearchTask, pool: List[Evidence] | None = None,
                     citations: List[Citation] | None = None,
                     state: ResearchState | None = None) -> ResearchReport:
    """LLM 结构化输出失败时的确定性降级报告：仍然保证章节化 + 引用溯源。"""
    pool = pool or []
    citations = citations or []
    state = state or {}
    sections: List[ReportSection] = []

    graph = {}  # Global legacy graph aggregates are not tenant-isolated.
    by_band = graph.get("by_price_band") or {}
    if by_band:
        content = "\n".join(
            f"- **{band}**：{'、'.join(names[:14])}" for band, names in by_band.items()
        )
        sections.append(
            ReportSection(heading="商品图谱概览", content=content,
                          citation_indices=list(range(min(6, len(citations)))))
        )

    if pool:
        content = "\n".join(
            f"- {c.quote}（[{c.index}]）" for c in citations[:12]
        )
        sections.append(
            ReportSection(heading="关键证据与发现", content=content,
                          citation_indices=list(range(min(12, len(citations)))))
        )

    hypotheses = (state.get("hypotheses") or []) if isinstance(state, dict) else []
    if hypotheses:
        content = "\n".join(
            f"- {h.hypothesis_text} → **{h.status.value}**（置信度 {h.confidence:.2f}，"
            f"支持 {h.support_count()} / 反驳 {h.refute_count()}）"
            for h in hypotheses
        )
        sections.append(
            ReportSection(heading="交叉核验结论", content=content,
                          citation_indices=list(range(min(6, len(citations)))))
        )

    if not sections:
        sections.append(
            ReportSection(
                heading="证据汇总",
                content="未能检索到足够证据支撑结论，建议补充商品资料源后重跑。",
                citation_indices=[],
            )
        )

    summary = (
        f"围绕「{task.question}」共汇聚 {len(pool)} 条证据、"
        f"{len(by_band)} 个价位段分组、{len(hypotheses)} 条待核验假设。"
        if pool else
        f"未能检索到足够证据支撑「{task.question}」的结论，建议补充商品资料源后重跑。"
    )
    return ResearchReport(
        title=f"{task.question}——商品知识问答报告",
        executive_summary=summary,
        sections=sections,
        risk_warnings=["部分结论证据不足，置信度偏低，建议人工复核",
                       "部分资料为合成兜底语料，请以官方参数表 / 手册为准"],
        recommendations=["补充商品资料源（手册 / 参数表 / FAQ）后重新生成答案",
                         "对低置信度核验假设补充针对性取证轮次"],
    )


def _graph_brief(state: ResearchState) -> str:
    graph = state.get("graph_result") or {}
    parts: List[str] = []
    by_band = graph.get("by_price_band") or {}
    for band, names in by_band.items():
        parts.append(f"{band}：{ '、'.join(names[:12]) }")
    stats = graph.get("stats") or {}
    if stats:
        parts.append(f"图谱规模：{stats.get('node_count', 0)} 节点 / {stats.get('edge_count', 0)} 关系")
    return "；".join(parts)


def _hypotheses_brief(state: ResearchState) -> str:
    rows = []
    for h in state.get("hypotheses", []) or []:
        status = h.status.value if hasattr(h.status, "value") else str(h.status)
        rows.append(f"- {h.hypothesis_text} → {status}（置信度 {h.confidence:.2f}）")
    return "\n".join(rows)


def _debate_brief(debate: DebateMatrix | None) -> str:
    if not debate:
        return "（未执行口径辩论）"
    bull = "；".join(p.claim for p in debate.bull_points[:3])
    bear = "；".join(p.claim for p in debate.bear_points[:3])
    return f"官方口径：{bull}\n用户反馈：{bear}\n裁决：{debate.verdict}"


def _overall_confidence(state: ResearchState) -> float:
    relevant = {
        "spec_query": "product",
        "sku_compare": "product",
        "compat_recommend": "compatibility",
        "after_sales": "after_sales",
        "coach_query": "coach",
    }
    role = relevant.get(getattr(state.get("plan"), "intent", ""))
    specialists = state.get("specialists") or []
    verified = [
        s for s in specialists
        if (s.get("role") == role or s.get("agent_name") == role)
        and s.get("verdict") == "supported"
        and s.get("findings")
    ]
    if verified:
        # Only bounded advisory confidence; semantic correctness still requires quality/review.
        return 0.7
    hypotheses = state.get("hypotheses", []) or []
    if not hypotheses:
        return 0.4
    return round(sum(h.confidence for h in hypotheses) / len(hypotheses), 3)


def _coverage(report: ResearchReport) -> float:
    """引用溯源覆盖率：带引用的段落 / 总段落。"""
    if not report.sections:
        return 0.0
    covered = sum(1 for s in report.sections if s.citation_indices)
    return round(covered / len(report.sections), 3)


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except Exception:  # noqa: BLE001
        return False


__all__ = ["report_generation_node"]
