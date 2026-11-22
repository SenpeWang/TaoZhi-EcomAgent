"""企业知识问答受控检索、多专家核验与答复发布状态机。"""
from __future__ import annotations

from typing import Literal

from taozhi.application.common.task_guard import traced_node
from .intent import plan_node
from .publication import publish_node
from .retrieval import prepare_evidence, read_business_snapshot, retrieval_node, retry_retrieval
from .specialists import domain_node, quality_node
from .state import State


def _after_planning(state: State) -> Literal["business_snapshot", "retrieval"]:
    return "business_snapshot" if state["live_fields"] else "retrieval"


def _after_retrieval(state: State) -> Literal["retrieval_retry", "prepare_evidence"]:
    if state["retrieval_decision"] in {"retry_weak_evidence", "retry_low_confidence"}:
        return "retrieval_retry"
    return "prepare_evidence"


def _after_evidence(state: State) -> Literal["specialists", "publication"]:
    return "specialists" if state["retrieval_decision"] == "run_specialists" else "publication"


def build_rag_workflow(checkpointer):
    """编译首次检索、单次补查、证据授权分流、协同核验与发布的有向无环图。"""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(State)
    nodes = [
        ("planning", plan_node),
        ("business_snapshot", read_business_snapshot),
        ("retrieval", retrieval_node),
        ("retrieval_retry", retry_retrieval),
        ("prepare_evidence", prepare_evidence),
        ("specialists", domain_node),
        ("quality", quality_node),
        ("publication", publish_node),
    ]
    for name, func in nodes:
        builder.add_node(name, traced_node(name, func))
    builder.add_edge(START, "planning")
    builder.add_conditional_edges("planning", _after_planning)
    builder.add_edge("business_snapshot", "publication")
    builder.add_conditional_edges("retrieval", _after_retrieval)
    builder.add_edge("retrieval_retry", "prepare_evidence")
    builder.add_conditional_edges("prepare_evidence", _after_evidence)
    builder.add_edge("specialists", "quality")
    builder.add_edge("quality", "publication")
    builder.add_edge("publication", END)
    return builder.compile(checkpointer=checkpointer)
