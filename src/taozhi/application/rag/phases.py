"""知识问答工作流的节点、阶段与合法转移契约。

命名约定：
- 节点是动作，使用“动词 + 宾语”；
- 阶段是动作完成后的状态，使用过去分词；
- 合法转移只在本模块定义，图装配、路由、轨迹与测试共用同一份口径。

状态轮转：

    question_analyzed ──fetch_live_data──▶ live_data_loaded ──compose_answer──▶ answer_composed
    question_analyzed ──retrieve_documents──▶ documents_retrieved
    documents_retrieved ──refine_query──▶ query_refined
    documents_retrieved ──prepare_context──▶ context_prepared
    query_refined ──prepare_context──▶ context_prepared
    context_prepared ──extract_claims──▶ claims_extracted
    context_prepared ──compose_answer──▶ answer_composed
    claims_extracted ──verify_claims──▶ claims_verified
    claims_verified ──compose_answer──▶ answer_composed
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class RagNode(str, Enum):
    """知识问答工作流的节点标识。"""

    ANALYZE_QUESTION = "analyze_question"
    FETCH_LIVE_DATA = "fetch_live_data"
    RETRIEVE_DOCUMENTS = "retrieve_documents"
    REFINE_QUERY = "refine_query"
    PREPARE_CONTEXT = "prepare_context"
    EXTRACT_CLAIMS = "extract_claims"
    VERIFY_CLAIMS = "verify_claims"
    COMPOSE_ANSWER = "compose_answer"


class RagPhase(str, Enum):
    """节点完成后写入 ``State.phase`` 的阶段值。"""

    QUESTION_ANALYZED = "question_analyzed"
    LIVE_DATA_LOADED = "live_data_loaded"
    DOCUMENTS_RETRIEVED = "documents_retrieved"
    QUERY_REFINED = "query_refined"
    CONTEXT_PREPARED = "context_prepared"
    CLAIMS_EXTRACTED = "claims_extracted"
    CLAIMS_VERIFIED = "claims_verified"
    ANSWER_COMPOSED = "answer_composed"


NODE_PHASE: dict[RagNode, RagPhase] = {
    RagNode.ANALYZE_QUESTION: RagPhase.QUESTION_ANALYZED,
    RagNode.FETCH_LIVE_DATA: RagPhase.LIVE_DATA_LOADED,
    RagNode.RETRIEVE_DOCUMENTS: RagPhase.DOCUMENTS_RETRIEVED,
    RagNode.REFINE_QUERY: RagPhase.QUERY_REFINED,
    RagNode.PREPARE_CONTEXT: RagPhase.CONTEXT_PREPARED,
    RagNode.EXTRACT_CLAIMS: RagPhase.CLAIMS_EXTRACTED,
    RagNode.VERIFY_CLAIMS: RagPhase.CLAIMS_VERIFIED,
    RagNode.COMPOSE_ANSWER: RagPhase.ANSWER_COMPOSED,
}

TRANSITIONS: dict[RagNode, frozenset[RagNode]] = {
    RagNode.ANALYZE_QUESTION: frozenset({RagNode.FETCH_LIVE_DATA, RagNode.RETRIEVE_DOCUMENTS}),
    RagNode.FETCH_LIVE_DATA: frozenset({RagNode.COMPOSE_ANSWER}),
    RagNode.RETRIEVE_DOCUMENTS: frozenset({RagNode.REFINE_QUERY, RagNode.PREPARE_CONTEXT}),
    RagNode.REFINE_QUERY: frozenset({RagNode.PREPARE_CONTEXT}),
    RagNode.PREPARE_CONTEXT: frozenset({RagNode.EXTRACT_CLAIMS, RagNode.COMPOSE_ANSWER}),
    RagNode.EXTRACT_CLAIMS: frozenset({RagNode.VERIFY_CLAIMS}),
    RagNode.VERIFY_CLAIMS: frozenset({RagNode.COMPOSE_ANSWER}),
    RagNode.COMPOSE_ANSWER: frozenset(),
}

RAG_GRAPH_VERSION = "v2"

_RETRY_DECISIONS = frozenset({"retry_weak_evidence", "retry_low_confidence"})


def _route(current: RagNode, target: RagNode) -> str:
    """校验目标节点属于当前节点的合法转移，并返回 LangGraph 节点 ID。"""
    if target not in TRANSITIONS[current]:
        raise ValueError(f"非法状态转移：{current.value} → {target.value}")
    return target.value


def route_after_analyze(state: Mapping[str, Any]) -> str:
    """问题分析后：存在实时业务字段时读取业务数据，否则检索授权资料。"""
    target = RagNode.FETCH_LIVE_DATA if state.get("live_fields") else RagNode.RETRIEVE_DOCUMENTS
    return _route(RagNode.ANALYZE_QUESTION, target)


def route_after_retrieve(state: Mapping[str, Any]) -> str:
    """首轮检索后：证据不足或置信度偏低时补查一次，否则准备授权上下文。"""
    target = (
        RagNode.REFINE_QUERY
        if state.get("retrieval_decision") in _RETRY_DECISIONS
        else RagNode.PREPARE_CONTEXT
    )
    return _route(RagNode.RETRIEVE_DOCUMENTS, target)


def route_after_prepare(state: Mapping[str, Any]) -> str:
    """上下文准备后：允许专家处理时抽取候选结论，否则直接生成答复。"""
    target = (
        RagNode.EXTRACT_CLAIMS
        if state.get("retrieval_decision") == "run_specialists"
        else RagNode.COMPOSE_ANSWER
    )
    return _route(RagNode.PREPARE_CONTEXT, target)