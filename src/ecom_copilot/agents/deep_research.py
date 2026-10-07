"""⑤ 交叉核验 Agent：hypothesis_tracker 核验假设 + 口径交叉辩论。

迁移自 SRE 根因诊断 Agent：
    根因假设 → 商品知识核验假设；验证证据从「日志 / 指标」变为
    「商品手册 / 参数表 / 详情页 / FAQ / 客服工单」。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from ..debate import DebateProtocol
from ..hypothesis import HypothesisTracker, sentiment_from_text
from ..hypothesis import HypothesisTracker, sentiment_from_text
from ..llm import ModelTier, get_llm
from ..llm.json_utils import extract_json
from ..observability import inc, log_event
from ..schemas.common import Evidence, PermissionContext, Sentiment
from ..schemas.research import DebateMatrix, HypothesisStatus, ResearchDepth
from .state import ResearchRuntime, ResearchState, append_trace

_HYPOTHESIS_PROMPT = """你是电商商品知识问答的核验负责人。围绕用户问题，提出 3 条**可被多文档证据交叉验证**的关键核验假设。

用户问题：{question}
关注产品线：{focus}
已有事实（来自商品图谱与检索）：
{context}

要求：
- 每条假设必须是明确、可证伪的判断句（如"防窥膜 C3 是否支持曲面屏机型""陶瓷膜 C5 是否适配华为 Mate X5""膜切机 MC-500 是否支持 C3/C5"），不超过 40 字；
- 覆盖参数口径、兼容适配、售后政策中的不同维度；
- 不要写"需要进一步核实"这类不可验证的表述；
- 不要编造资料中不存在的参数数字，用相对表述（如"更高/相当/一致"）。
直接输出 JSON，不要任何解释：{{"hypotheses":["","",""]}}
"""

_SENTIMENT_PROMPT = """判断下列证据对核验假设的立场。

核验假设：{hypothesis}
证据列表：
{evidence}

对每条证据输出 support（支持）/ refute（反驳）/ neutral（中性）。
证据没有直接支持或反驳该假设时必须标注 neutral；主题相关不能等同于支持。
直接输出 JSON：{{"labels":["support","refute","neutral"]}}
"""


def deep_research_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    settings = runtime.settings
    task = state["task"]
    tracker: HypothesisTracker = runtime.tracker  # type: ignore[assignment]
    started = time.perf_counter()
    permission: PermissionContext = state.get("permission") or PermissionContext()

    # 复用已有假设（checkpointer 恢复场景）
    if tracker is not None and not tracker.all() and state.get("hypotheses"):
        for item in state["hypotheses"]:
            tracker.hypotheses[item.id] = item

    # ── 第 0 轮：生成核验假设 ──
    if not state.get("hypotheses"):
        hypotheses = _generate_hypotheses(state)
        if tracker is not None:
            for text in hypotheses:
                tracker.create(text, category=_hypothesis_category(text))
        state["hypotheses"] = tracker.all() if tracker is not None else []
        log_event("hypotheses_generated", task_id=task.task_id, count=len(hypotheses))

    # ── 取证轮次 ──
    round_no = int(state.get("research_round", 0)) + 1
    if tracker is not None:
        tracker.next_round()

    evidence_pool: List[Evidence] = list(state.get("evidence_pool", []))
    for hypothesis in (tracker.all() if tracker else []):
        if hypothesis.status not in (HypothesisStatus.PENDING, HypothesisStatus.INVESTIGATING,
                                     HypothesisStatus.INCONCLUSIVE):
            continue
        if tracker is not None:
            tracker.start_investigation(hypothesis)
        query = f"{task.question} {hypothesis.hypothesis_text}"
        hits = _retrieve(runtime, query, permission, top_k=6)
        if not hits:
            continue
        labelled = _label_sentiment(hypothesis.hypothesis_text, hits)
        if tracker is not None:
            tracker.add_evidence(hypothesis, labelled)
        evidence_pool.extend(labelled)

    # ── 评估 ──
    if tracker is not None:
        tracker.evaluate_all()

    hypotheses = tracker.all() if tracker is not None else list(state.get("hypotheses", []))
    dedup_pool = _dedup(evidence_pool)

    # ── 口径交叉辩论 ──
    debate: Optional[DebateMatrix] = state.get("debate")
    depth = task.depth.value if hasattr(task.depth, "value") else str(task.depth)
    if depth != ResearchDepth.QUICK.value and _should_debate(state, round_no, depth):
        topic = task.question
        evidence_for_debate = _dedup(_relevant_evidence(runtime, topic, permission, dedup_pool))
        protocol = DebateProtocol(max_rounds=settings.max_debate_rounds)
        debate = protocol.run(topic, evidence_for_debate[:12], rounds=1)
        inc("debate.executed")

    # ── 人工复核（human-in-the-loop） ──
    review_required = any(h.needs_human_review for h in hypotheses)
    elapsed = time.perf_counter() - started
    append_trace(
        state, "deep_research",
        f"第 {round_no} 轮交叉取证：{_status_brief(hypotheses)}" + ("；已触发人工复核" if review_required else ""),
        elapsed,
        hypotheses=[h.hypothesis_text[:60] for h in hypotheses],
    )

    if tracker is not None:
        try:
            tracker.save()
        except Exception:  # noqa: BLE001
            pass

    return {
        "hypotheses": hypotheses,
        "evidence_pool": dedup_pool[:300],
        "debate": debate,
        "research_round": round_no,
        "trace": state.get("trace", []),
        "review_decision": state.get("review_decision", ""),
        "metrics": {
            **state.get("metrics", {}),
            f"round_{round_no}_confirmed": sum(
                1 for h in hypotheses if h.status == HypothesisStatus.CONFIRMED),
            "review_required": review_required,
        },
    }


# ───────────────────────── 辅助 ─────────────────────────


def _hypothesis_category(text: str) -> str:
    """将核验假设归入「参数口径 | 兼容适配 | 售后政策」三类之一。"""
    if any(k in text for k in ("适配", "兼容", "通用", "能不能用", "替代", "升级")):
        return "兼容适配"
    if any(k in text for k in ("保修", "质保", "退货", "售后", "维修", "政策")):
        return "售后政策"
    return "参数口径"


def _generate_hypotheses(state: ResearchState) -> List[str]:
    task = state["task"]
    if not get_llm().settings.has_llm:
        return ["不同批次资料参数口径可能不一致，需交叉核对官方参数表与详情页",
                "不同来源的适配关系口径可能不一致，需以官方适配表为准交叉核对",
                "不同渠道的售后政策描述可能不一致，需以官方售后政策页为准"]
    context_parts: List[str] = []
    extraction = state.get("extraction")
    if extraction is not None:
        context_parts.append(
            "商品实体：" + "、".join(e.name for e in extraction.entities[:20])
        )
        context_parts.append(
            "商品适配关系：" + "；".join(
                f"{t.subject}-{t.predicate.value}-{t.obj}" for t in extraction.triples[:12]
            )
        )
    for doc in state.get("documents", [])[:5]:
        context_parts.append(f"资料《{doc.title}》：{(doc.raw_text or '')[:300]}")
    prompt = _HYPOTHESIS_PROMPT.format(
        question=task.question,
        focus=task.product_line or task.category or "全产品线",
        context="\n".join(context_parts)[:4000] or "（暂无资料，请基于商品常识提出）",
    )
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.STRONG, max_tokens=1200)
        data = extract_json(raw, expect="object") or {}
        items = [str(x).strip() for x in (data.get("hypotheses") or []) if str(x).strip()]
    except Exception:  # noqa: BLE001
        items = []
    return items[:3] or [f"{task.question}的关键判断成立"]


def _retrieve(runtime: ResearchRuntime, query: str, permission: PermissionContext,
              top_k: int = 6) -> List[Evidence]:
    if runtime.engine is None:
        return []
    try:
        return runtime.engine.retrieve_evidence(query, top_k=top_k, permission=permission)
    except Exception:  # noqa: BLE001
        return []


def _relevant_evidence(runtime: ResearchRuntime, topic: str, permission: PermissionContext,
                       pool: List[Evidence]) -> List[Evidence]:
    if pool:
        return pool
    return _retrieve(runtime, topic, permission, top_k=10)


def _label_sentiment(hypothesis: str, evidence: List[Evidence]) -> List[Evidence]:
    """用 LLM 判定每条证据对假设的立场，失败时回退规则。"""
    if not evidence:
        return []
    if not get_llm().settings.has_llm:
        for item in evidence:
            item.sentiment = sentiment_from_text(item.quote)
        return evidence
    listing = "\n".join(
        f"[{i}] {item.doc_name}：{item.quote[:180]}" for i, item in enumerate(evidence[:8])
    )
    prompt = _SENTIMENT_PROMPT.format(hypothesis=hypothesis, evidence=listing)
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=600)
        data = extract_json(raw, expect="object") or {}
        labels = data.get("labels") or []
    except Exception:  # noqa: BLE001
        labels = []
    for index, item in enumerate(evidence):
        label = str(labels[index]).lower() if index < len(labels) else ""
        if label.startswith("support"):
            item.sentiment = Sentiment.SUPPORT
        elif label.startswith("refute"):
            item.sentiment = Sentiment.REFUTE
        else:
            item.sentiment = Sentiment.NEUTRAL
    return evidence


def _dedup(pool: List[Evidence]) -> List[Evidence]:
    seen = set()
    result = []
    for item in pool:
        key = (item.doc_name, item.quote[:80])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _should_debate(state: ResearchState, round_no: int, depth: str) -> bool:
    if state.get("debate") is not None:
        return False
    if depth == ResearchDepth.DEEP.value:
        return round_no >= 2
    return round_no >= 1


def _status_brief(hypotheses: List[Any]) -> str:
    counts: Dict[str, int] = {}
    for h in hypotheses:
        status = h.status.value if hasattr(h.status, "value") else str(h.status)
        counts[status] = counts.get(status, 0) + 1
    return "，".join(f"{k} {v}" for k, v in counts.items()) or "无假设"


__all__ = ["deep_research_node"]
