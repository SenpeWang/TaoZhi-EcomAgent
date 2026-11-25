"""知识问答工作流的节点、阶段与合法转移契约。"""
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from taozhi.application.rag import intent, publication
from taozhi.application.rag.phases import (
    NODE_PHASE,
    RagNode,
    RagPhase,
    TRANSITIONS,
    route_after_analyze,
    route_after_prepare,
    route_after_retrieve,
)
from taozhi.application.rag.workflow import build_rag_workflow
from taozhi.orchestration.decision import DecisionPath


def test_every_node_has_exactly_one_phase():
    """每个节点对应唯一阶段，且所有阶段都被使用。"""
    assert set(NODE_PHASE) == set(RagNode)
    assert set(NODE_PHASE.values()) == set(RagPhase)


def test_transitions_form_a_single_terminal_dag():
    """转移图连通、只有定稿节点没有出边。"""
    assert TRANSITIONS[RagNode.COMPOSE_ANSWER] == frozenset()
    assert all(TRANSITIONS[node] for node in RagNode if node is not RagNode.COMPOSE_ANSWER)

    reachable = {RagNode.ANALYZE_QUESTION}
    frontier = [RagNode.ANALYZE_QUESTION]
    while frontier:
        for target in TRANSITIONS[frontier.pop()]:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert reachable == set(RagNode)


def test_routes_only_return_declared_targets():
    """三个路由函数在代表性状态下只返回转移表声明的目标。"""
    assert route_after_analyze({"live_fields": {"stock": {"available": ""}}}) == RagNode.FETCH_LIVE_DATA.value
    assert route_after_analyze({}) == RagNode.RETRIEVE_DOCUMENTS.value
    for decision in ("retry_weak_evidence", "retry_low_confidence"):
        assert route_after_retrieve({"retrieval_decision": decision}) == RagNode.REFINE_QUERY.value
    for decision in ("sufficient_evidence", "no_authorized_evidence", "external_processing_blocked", "no_finance_evidence"):
        assert route_after_retrieve({"retrieval_decision": decision}) == RagNode.PREPARE_CONTEXT.value
    assert route_after_prepare({"retrieval_decision": "run_specialists"}) == RagNode.EXTRACT_CLAIMS.value
    assert route_after_prepare({"retrieval_decision": "no_authorized_evidence"}) == RagNode.COMPOSE_ANSWER.value


def test_compiled_graph_contains_every_contract_node():
    """编译出的 LangGraph 节点集合与契约一致。"""
    nodes = set(build_rag_workflow(InMemorySaver()).get_graph().nodes)
    assert {node.value for node in RagNode} <= nodes


def test_analysis_and_answer_nodes_return_their_phase(monkeypatch):
    """节点直接返回结果时也携带对应阶段，便于轨迹与测试共用。"""
    job = {"question": "资料说明", "input_level": 1, "payload": {}}
    monkeypatch.setattr(intent, "validate_task_execution", lambda *_: (None, job))
    monkeypatch.setattr(
        intent,
        "get_decision_engine",
        lambda: SimpleNamespace(choice=lambda **_: SimpleNamespace(selected=["product"], path=DecisionPath.FAST_SEMANTIC)),
    )
    analysis = intent.analyze_question({})
    assert analysis["phase"] == RagPhase.QUESTION_ANALYZED.value

    monkeypatch.setattr(publication, "validate_task_execution", lambda *_: (None, job))
    monkeypatch.setattr(publication, "get_settings", lambda: SimpleNamespace(has_llm=False))
    answer = publication.compose_answer(
        {
            "question": "资料说明", "evidence": [], "business": [], "findings": [], "missing": [],
            "hard_block": True, "external_blocked": False, "input_level": 1, "sources": [], "usage": [],
            "model_calls": 0, "token_usage": {}, "degraded": {},
        }
    )
    assert answer["phase"] == RagPhase.ANSWER_COMPOSED.value