"""验证企业知识问答 RAG 与 ChatBI 受控问数工作流已按功能完全解耦。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from taozhi.application.bi.intent import detect_data_intent
from taozhi.application.bi.state import BiState
from taozhi.application.bi.workflow import build_bi_workflow
from taozhi.application.rag.state import State as RagState
from taozhi.application.rag.workflow import build_rag_workflow


def test_rag_and_bi_graphs_are_structurally_decoupled():
    """验证 RAG 与 BI 状态契约与 LangGraph 节点拓扑互不耦合。"""
    assert "data_intent" not in RagState.__annotations__
    assert "data_result" not in RagState.__annotations__
    assert "evidence" not in BiState.__annotations__
    assert "selected_agents" not in BiState.__annotations__

    rag_graph = build_rag_workflow(MemorySaver())
    rag_nodes = set(rag_graph.get_graph().nodes.keys())
    assert {"analyze_question", "retrieve_documents", "extract_claims", "verify_claims", "compose_answer"}.issubset(rag_nodes)
    assert "data_query" not in rag_nodes

    bi_graph = build_bi_workflow(MemorySaver())
    bi_nodes = set(bi_graph.get_graph().nodes.keys())
    assert {"bi_intent", "data_query", "bi_publish"}.issubset(bi_nodes)
    assert "retrieve_documents" not in bi_nodes
    assert "extract_claims" not in bi_nodes


def test_detect_data_intent_skips_non_bi_and_flags_denied_metric():
    """验证问数意图识别对普通知识问题返回 None，对越权指标返回 denied。"""
    principal = SimpleNamespace(tenant_id="t1", id="u1")
    conn = MagicMock()

    assert detect_data_intent(conn, principal, "这款切割机的切幅和材质说明是什么？") is None

    restricted_metric = {
        "id": "m_profit",
        "metric_key": "gross_margin",
        "name": "毛利率",
        "aliases": ["毛利"],
        "level": 3,
        "dimensions": [],
        "description": "毛利率指标",
    }
    with (
        patch("taozhi.application.bi.intent.list_published_metrics", return_value=[restricted_metric]),
        patch("taozhi.application.bi.intent.metric_execution.list_metrics", return_value=[]),
    ):
        denied = detect_data_intent(conn, principal, "近7天毛利率是多少？")
    assert denied is not None
    assert denied["denied"] is True
    assert "受限指标" in denied["reason"]


def test_bi_workflow_executes_independently_end_to_end():
    """验证 ChatBI 工作流可脱离 RAG 独立完成指标查询与结果渲染。"""
    principal = SimpleNamespace(tenant_id="t1", id="u1")
    job = {"id": "job_bi_1", "run_version": 1, "question": "近7天销售额是多少", "input_level": 1}
    metric = {
        "id": "m_gmv",
        "metric_key": "gmv",
        "name": "销售额",
        "unit": "元",
        "level": 1,
        "description": "近7天成交总额",
    }
    dummy_conn = MagicMock()
    dummy_conn.__enter__ = MagicMock(return_value=dummy_conn)
    dummy_conn.__exit__ = MagicMock(return_value=False)

    flow = build_bi_workflow(MemorySaver())
    with (
        patch("taozhi.application.bi.workflow.validate_task_execution", return_value=(principal, job)),
        patch("taozhi.application.bi.workflow.database_connection", return_value=dummy_conn),
        patch("taozhi.application.bi.workflow.get_published_metric_by_id", return_value=metric),
        patch("taozhi.application.bi.workflow.can_read_metric", return_value=True),
        patch(
            "taozhi.application.bi.workflow.metric_execution.execute_metric",
            return_value=(["日期", "销售额"], [["2026-11-19", 12800]], {"unit": "元", "elapsed_ms": 12, "level": 1}),
        ),
    ):
        final_state = flow.invoke(
            {
                "job_id": "job_bi_1",
                "run_version": 1,
                "data_intent": {"metric_key": "gmv", "metric_id": "m_gmv", "intent": {"metric_key": "gmv"}},
            },
            {"configurable": {"thread_id": "job_bi_1:1"}},
        )

    assert final_state["hard_block"] is False
    assert "销售额" in final_state["answer"]
    assert "12800" in final_state["answer"]
    assert final_state["data_result"]["metric_key"] == "gmv"
    assert final_state["sources"] == [{"kind": "metric", "metric_id": "m_gmv", "tenant_id": "t1"}]
