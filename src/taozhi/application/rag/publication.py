"""按授权原文、已核验结论或公开参考发布知识答复。"""
from __future__ import annotations

from contextlib import closing

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.observability import usage_scope
from taozhi.application.common.task_guard import merge_token_usage, validate_task_execution
from taozhi.config import get_settings
from taozhi.domain.knowledge_answer import (
    AnswerBasis,
    PublicSource,
    allows_general_knowledge_fallback,
    current_shanghai_clock,
    resolve_evidence_gap,
)
from taozhi.domain.privacy import redact
from taozhi.domain.question_context import recalls_previous_evidence, refers_to_previous_question
from taozhi.domain.rag_verification import cited_paragraphs, document_addresses_question
from taozhi.observability.tracing import record_diagnostic
from .phases import RagPhase
from .prompts import RAG_PROMPTS
from .state import State

_BUSINESS_FIELDS = {
    "available": "可用库存", "price_minor": "销售价格（分）", "status": "订单状态", "as_of": "数据时间",
    "sku_id": "商品编号", "order_id": "订单编号", "currency": "币种",
}
_BUSINESS_VALUES = {
    "CNY": "人民币", "pending": "待支付", "paid": "已支付", "shipped": "已发货",
    "delivered": "已签收", "cancelled": "已取消", "refunded": "已退款",
    "processing": "处理中", "completed": "已完成", "refunding": "退款中", "closed": "已关闭", "returned": "已退货",
}
_VERIFICATION_ACTIONS = {
    "model_unconfigured": "如需自动核验，请联系管理员配置知识问答模型后重新提问。",
    "budget_exhausted": "如需自动核验，请聚焦一个问题重新提问，或请管理员核对任务调用预算。",
    "model_unavailable": "如需自动核验，请在模型服务恢复后重新提问。",
}


def _snapshot_text(snapshot: dict) -> str:
    """把业务快照记录渲染为带中文字段名的实时数据文本。

    Args:
        snapshot: 业务快照，含 record 字段。

    Returns:
        逐行列出已知业务字段的实时数据说明文本。
    """
    rows = [
        f"{_BUSINESS_FIELDS[key]}：{_BUSINESS_VALUES.get(str(value), value)}"
        for key, value in snapshot["record"].items() if key in _BUSINESS_FIELDS
    ]
    return "实时业务数据：\n" + "\n".join(rows)


def _answer_with_general_knowledge(question: str, state: State) -> tuple[str, dict] | None:
    """在非企业专有事项且已授权外部模型时，使用通用模型能力补充回答。"""
    if not get_settings().has_llm:
        return None
    system, _ = RAG_PROMPTS.render(
        "rag.general.fallback",
        {"current_clock": current_shanghai_clock()},
    )
    with usage_scope() as usage:
        try:
            with closing(LLMClient()) as llm:
                reply = llm.chat(
                    redact(question),
                    tier=ModelTier.FAST,
                    system=system or "",
                    max_tokens=1024,
                    allow_overseas=False,
                ).strip()
        except Exception as exc:
            record_diagnostic("model.general_fallback", "skipped", exc)
            return None
    if not reply:
        return None
    merged_usage = merge_token_usage(
        state.get("token_usage"),
        "general_knowledge",
        usage.to_dict(),
    )
    return reply, merged_usage


def compose_answer(state: State) -> dict:
    """确保公开参考、人工核验原文和内部结论各有明确来源与审核状态。"""
    _, job = validate_task_execution(state, "发布前校验权限与来源", 95)
    question = state["question"]
    evidence = state["evidence"]
    business = state["business"]
    missing = list(state["missing"])
    snapshots = [_snapshot_text(item) for item in business if item.get("available")]
    public_sources: list[PublicSource] = []
    guidance = ""
    verification_issue = state.get("verification_issue")
    excerpt_indices = []
    if (state["external_blocked"] or verification_issue) and not state["hard_block"] and not state["findings"]:
        excerpt_indices = [
            i for i, doc in enumerate(evidence)
            if document_addresses_question(question, doc["quote"], doc["title"])
        ][:5]
    basis: AnswerBasis
    extra_token_usage = None
    extra_model_calls = 0
    if state["findings"] and not state["hard_block"] and not state["external_blocked"]:
        basis = "internal_evidence"
        answer = "".join(cited_paragraphs(question, evidence, state["findings"]))
        if missing:
            guidance = resolve_evidence_gap(question, allow_public_reference=False)["guidance"]
    elif excerpt_indices:
        basis = "source_excerpt"
        lead = ("当前资料未获外部 AI 处理许可。" if state["external_blocked"] else "已找到与问题相关的资料，自动核验未完成。")
        answer = lead + "以下为授权原文片段，请人工核验。\n\n" + "\n\n".join(
            f"{evidence[i]['quote']} [{i + 1}]" for i in excerpt_indices)
        guidance = "请打开对应版本的原文，核对适用商品、条件和生效时间。"
        if verification_issue:
            guidance += _VERIFICATION_ACTIONS[verification_issue]
    elif snapshots and not state["hard_block"]:
        basis = "business_snapshot"
        answer = ""
        guidance = "业务快照仅确认下列已返回的字段。涉及其他条款或承诺时，请核对相应内部资料。"
    else:
        allow_public = bool(
            job["payload"].get("allow_public_reference", False) and not business and not state.get("live_fields")
            and not state.get("prior_question_id") and not state.get("has_recalled_context")
            and not refers_to_previous_question(question)
            and not recalls_previous_evidence(question, "")
        )
        gap = resolve_evidence_gap(question, allow_public_reference=allow_public)
        basis, answer, guidance, public_sources = gap["answer_basis"], gap["answer"], gap["guidance"], gap["public_sources"]
        if basis == "public_reference":
            missing = []
        elif (
            job["payload"].get("allow_general_knowledge", False)
            and job["payload"].get("allow_external", True)
            and job["input_level"] < 3
            and not business
            and not state.get("live_fields")
            and not state.get("prior_question_id")
            and not state.get("has_recalled_context")
            and not refers_to_previous_question(question)
            and not recalls_previous_evidence(question, "")
            and allows_general_knowledge_fallback(question)
        ):
            general = _answer_with_general_knowledge(question, state)
            if general is not None:
                answer, extra_token_usage = general
                basis = "general_knowledge"
                guidance = "以下内容由大模型通用能力生成，与企业内部知识库及官方参考无关，不作为本店政策或商品承诺依据。"
                missing = []
                extra_model_calls = 1

    parts = [answer, *snapshots, *(item["message"] for item in business if not item.get("available"))]
    substantive = basis != "insufficient_evidence" or bool(snapshots)
    levels = [job["input_level"], *(item["level"] for item in evidence),
              *(item.get("source", {}).get("level", 1) for item in business)]
    output = {
        "answer": "\n\n".join(part for part in parts if part), "input_level": max(levels),
        "answer_basis": basis, "guidance": guidance, "public_sources": public_sources, "missing": missing,
        "excerpt_indices": excerpt_indices,
        "hard_block": state["hard_block"] and basis not in ("public_reference", "general_knowledge"),
        "review": bool(job["payload"].get("require_review") and (substantive or not state["hard_block"])),
        "phase": RagPhase.ANSWER_COMPOSED.value,
    }
    if extra_token_usage is not None:
        output["token_usage"] = extra_token_usage
        output["model_calls"] = int(state.get("model_calls", 0)) + extra_model_calls
    return output
