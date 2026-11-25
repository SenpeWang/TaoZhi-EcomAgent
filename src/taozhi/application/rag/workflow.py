"""企业知识问答受控检索、多专家核验与答复发布状态机。"""
from __future__ import annotations

from taozhi.application.common.task_guard import traced_node
from .intent import analyze_question
from .phases import (
    TRANSITIONS,
    RagNode,
    route_after_analyze,
    route_after_prepare,
    route_after_retrieve,
)
from .publication import compose_answer
from .retrieval import fetch_live_data, prepare_context, refine_query, retrieve_documents
from .specialists import extract_claims, verify_claims
from .state import State


def build_rag_workflow(checkpointer):
    """编译“分析 → 取数或检索 → 补查 → 准备上下文 → 抽取核验 → 定稿”的有向无环图。"""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(State)
    for node, func in (
        (RagNode.ANALYZE_QUESTION, analyze_question),
        (RagNode.FETCH_LIVE_DATA, fetch_live_data),
        (RagNode.RETRIEVE_DOCUMENTS, retrieve_documents),
        (RagNode.REFINE_QUERY, refine_query),
        (RagNode.PREPARE_CONTEXT, prepare_context),
        (RagNode.EXTRACT_CLAIMS, extract_claims),
        (RagNode.VERIFY_CLAIMS, verify_claims),
        (RagNode.COMPOSE_ANSWER, compose_answer),
    ):
        builder.add_node(node.value, traced_node(node.value, func))

    builder.add_edge(START, RagNode.ANALYZE_QUESTION.value)
    builder.add_conditional_edges(
        RagNode.ANALYZE_QUESTION.value,
        route_after_analyze,
        [node.value for node in TRANSITIONS[RagNode.ANALYZE_QUESTION]],
    )
    builder.add_edge(RagNode.FETCH_LIVE_DATA.value, RagNode.COMPOSE_ANSWER.value)
    builder.add_conditional_edges(
        RagNode.RETRIEVE_DOCUMENTS.value,
        route_after_retrieve,
        [node.value for node in TRANSITIONS[RagNode.RETRIEVE_DOCUMENTS]],
    )
    builder.add_edge(RagNode.REFINE_QUERY.value, RagNode.PREPARE_CONTEXT.value)
    builder.add_conditional_edges(
        RagNode.PREPARE_CONTEXT.value,
        route_after_prepare,
        [node.value for node in TRANSITIONS[RagNode.PREPARE_CONTEXT]],
    )
    builder.add_edge(RagNode.EXTRACT_CLAIMS.value, RagNode.VERIFY_CLAIMS.value)
    builder.add_edge(RagNode.VERIFY_CLAIMS.value, RagNode.COMPOSE_ANSWER.value)
    builder.add_edge(RagNode.COMPOSE_ANSWER.value, END)
    return builder.compile(checkpointer=checkpointer)
