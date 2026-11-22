"""企业知识问答 RAG 工作流状态结构。"""
from __future__ import annotations

from typing import TypedDict

from taozhi.domain.knowledge_answer import AnswerBasis, PublicSource, VerificationIssue
from taozhi.domain.rag_verification import RetrievalDecision


class State(TypedDict, total=False):
    """企业知识问答 LangGraph 状态契约。"""

    job_id: str
    run_version: int
    question: str
    selected_agents: list
    live_fields: dict[str, dict[str, str]]
    has_recalled_context: bool
    evidence: list
    prior_question_id: str | None
    retrieval_decision: RetrievalDecision
    retrieval_search_count: int
    findings: list
    verification_issue: VerificationIssue
    excerpt_indices: list[int]
    missing: list
    model_calls: int
    hard_block: bool
    answer: str
    answer_basis: AnswerBasis
    guidance: str
    public_sources: list[PublicSource]
    sources: list
    external_blocked: bool
    business: list
    review: bool
    input_level: int
    usage: list
    timing: dict
    degraded: dict
    token_usage: dict
