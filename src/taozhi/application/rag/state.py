"""企业知识问答 RAG 工作流状态结构。"""
from __future__ import annotations

from typing import TypedDict

from taozhi.domain.knowledge_answer import AnswerBasis, PublicSource, VerificationIssue
from taozhi.domain.rag_verification import RetrievalDecision
from .phases import RagPhase


class State(TypedDict, total=False):
    """企业知识问答 LangGraph 状态契约。

    ``phase`` 记录当前已完成节点写入的阶段，各阶段新增字段如下：
    ``question_analyzed`` 写 ``selected_agents`` 与 ``live_fields``；
    ``live_data_loaded`` 写 ``business`` 与业务来源；
    ``documents_retrieved`` 写 ``evidence`` 与 ``retrieval_decision``；
    ``query_refined`` 更新 ``evidence`` 与 ``retrieval_search_count``；
    ``context_prepared`` 写 ``evidence``、``sources``、``business`` 与 ``retrieval_decision``；
    ``claims_extracted`` 写 ``findings`` 与 ``missing``；
    ``claims_verified`` 写 ``findings`` 与 ``review``；
    ``answer_composed`` 写 ``answer``、``answer_basis``、``review`` 与 ``hard_block``。
    """

    job_id: str
    run_version: int
    phase: RagPhase
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
