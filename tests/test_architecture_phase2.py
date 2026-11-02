"""阶段二架构重构验收测试：编排层 5 节点迭代闭环、确定性防死锁与外部搜索兜底。"""
from __future__ import annotations

import pytest
from taozhi.agent.tools.base import ToolResult
from taozhi.observability.tracer import active_trace
from taozhi.orchestration.controller import DeterministicController
from taozhi.orchestration.loop import AgenticRAGOrchestrator
from taozhi.orchestration.router import QueryRoute, QueryRouter
from taozhi.orchestration.state import OrchestrationState
from taozhi.schemas.rag import DocumentChunk, RetrievalCandidate


def test_query_router():
    """测试确定性查询路由分流规则。"""
    router = QueryRouter()
    assert router.route("你好，请问在吗？") == QueryRoute.DIRECT_ANSWER
    assert router.route("查看1688竞品价格与供货商报价") == QueryRoute.EXTERNAL_SEARCH
    assert router.route("手机壳的微缝公差是多少？") == QueryRoute.INTERNAL_RAG


def test_deterministic_controller_guards():
    """测试确定性控制守护器：迭代预算硬限制与改写死锁检测。"""
    ctrl = DeterministicController(max_iterations=2)
    state = OrchestrationState(
        session_id="s1",
        trace_id="t1",
        original_query="q",
        current_query="q",
        iteration_count=2,
    )
    ok, reason = ctrl.check_iteration_budget(state)
    assert not ok
    assert reason == "MAX_ITERATIONS_EXCEEDED"

    # 死锁检测
    history = ["智能保温杯", "保温杯 规则"]
    assert ctrl.detect_rewrite_deadlock("智能保温杯", history)
    assert ctrl.detect_rewrite_deadlock("智能保温杯 ", history)
    assert not ctrl.detect_rewrite_deadlock("电热水壶", history)


def test_orchestration_loop_direct_sufficient():
    """测试 5 节点迭代闭环：初次检索命中即流向 Answer 节点。"""
    class MockRetrievalTool:
        def __call__(self, params, context=None):
            return ToolResult(
                success=True,
                output={
                    "candidates": [
                        {
                            "chunk": {
                                "chunk_id": "c1",
                                "doc_id": "d1",
                                "doc_title": "产品规格",
                                "content": "微缝公差控制在 0.05mm 之内，适配全部高精模具。",
                            },
                            "score": 0.88,
                        }
                    ]
                },
            )

    orchestrator = AgenticRAGOrchestrator(retrieval_tool=MockRetrievalTool())
    state = OrchestrationState(
        session_id="s_test",
        trace_id="tr_test",
        original_query="手机壳微缝公差是多少？",
        current_query="手机壳微缝公差是多少？",
    )

    with active_trace() as tr:
        res_state = orchestrator.run(state)

    assert res_state.next_node == "end"
    assert res_state.evaluation.is_sufficient
    assert res_state.iteration_count == 0
    assert not res_state.is_fallback
    assert len(res_state.citations) >= 1
    assert "0.05mm" in res_state.answer


def test_orchestration_loop_rewrite_cycle():
    """测试 5 节点迭代闭环：首次检索不足 -> 触发改写 -> 二次检索命中 -> 成功回答。"""
    call_count = 0

    class IterativeRetrievalTool:
        def __call__(self, params, context=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次返回空切片
                return ToolResult(success=True, output={"candidates": []})
            # 第二次改写后返回高相关切片
            return ToolResult(
                success=True,
                output={
                    "candidates": [
                        {
                            "chunk": {
                                "chunk_id": "c2",
                                "doc_id": "d2",
                                "doc_title": "售后退换规范",
                                "content": "收到货 7 天内无理由退货，非质量问题运费自理。",
                            },
                            "score": 0.90,
                        }
                    ]
                },
            )

    orchestrator = AgenticRAGOrchestrator(retrieval_tool=IterativeRetrievalTool())
    state = OrchestrationState(
        session_id="s_iter",
        trace_id="tr_iter",
        original_query="请问有没有关于无理由退换货的规定？",
        current_query="请问有没有关于无理由退换货的规定？",
    )

    with active_trace() as tr:
        res_state = orchestrator.run(state)

    assert res_state.next_node == "end"
    assert res_state.iteration_count == 1
    assert len(res_state.rewrite_history) == 1
    assert res_state.evaluation.is_sufficient
    assert "7天" in res_state.answer or "7 天" in res_state.answer or "退货" in res_state.answer


def test_orchestration_loop_external_fallback():
    """测试 5 节点迭代闭环：多次检索不足且改写超限时，受控流转至外部搜索兜底。"""
    class EmptyRetrievalTool:
        def __call__(self, params, context=None):
            return ToolResult(success=True, output={"candidates": []})

    class MockWebSearchTool:
        def __call__(self, params, context=None):
            return ToolResult(
                success=True,
                output={
                    "candidates": [
                        {
                            "chunk": {
                                "chunk_id": "w1",
                                "doc_id": "ext1",
                                "doc_title": "行业调研新闻",
                                "content": "近期电商大盘热销爆款榜单数据披露。",
                            },
                            "score": 0.70,
                        }
                    ]
                },
            )

    ctrl = DeterministicController(max_iterations=1)
    orchestrator = AgenticRAGOrchestrator(
        retrieval_tool=EmptyRetrievalTool(),
        web_search_tool=MockWebSearchTool(),
        controller=ctrl,
    )
    state = OrchestrationState(
        session_id="s_fall",
        trace_id="tr_fall",
        original_query="极度偏僻生僻词",
        current_query="极度偏僻生僻词",
    )

    with active_trace() as tr:
        res_state = orchestrator.run(state)

    assert res_state.next_node == "end"
    assert res_state.is_fallback
    assert len(res_state.candidates) == 1
    assert "行业调研新闻" in res_state.answer or "ext1" in str(res_state.citations)