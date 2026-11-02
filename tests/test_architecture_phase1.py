"""阶段一架构重构验收测试：最小闭环、契约完整性、前置权限过滤与树状 Trace。"""
from __future__ import annotations

import pytest
from taozhi.agent.tools.base import BaseTool, ToolContext, ToolResult
from taozhi.agent.tools.retrieval_tool import KnowledgeRetrievalTool
from taozhi.api.chat import run_chat_minimal_loop
from taozhi.observability.tracer import TraceCollector, active_trace
from taozhi.rag.retrieval.context_builder import ContextBuilder
from taozhi.schemas.chat import ChatRequest, ChatResponse
from taozhi.schemas.rag import DocumentChunk, RetrievalCandidate, RetrievalFilter, RetrievalRequest


def test_schemas_validation():
    """测试接入层与 RAG 核心数据契约的校验逻辑。"""
    req = ChatRequest(question="苹果手机壳有微缝公差吗？", temperature=0.5)
    assert req.question == "苹果手机壳有微缝公差吗？"
    assert req.temperature == 0.5
    assert not req.stream

    chunk = DocumentChunk(
        chunk_id="chk_001",
        doc_id="doc_specs",
        doc_title="商品规格白皮书",
        content="全包保护壳边框公差控制在 0.05mm 之内。",
        line_start=12,
        line_end=15,
    )
    assert chunk.doc_id == "doc_specs"
    assert chunk.line_start == 12

    filt = RetrievalFilter(tenant_id="tenant_main", max_security_level=2)
    assert filt.max_security_level == 2


def test_trace_collector_tree_structure():
    """测试全链路 TraceCollector 的父子 Span 嵌套结构与耗时度量。"""
    with active_trace(tenant_id="test_tenant", user_id="user_007") as collector:
        assert collector.context.tenant_id == "test_tenant"

        with collector.span("orchestration.decide", kind="agent") as s1:
            s1.output_data = {"action": "retrieve"}

            with collector.span("agent.tool.search", kind="tool") as s2:
                s2.output_data = {"found": 3}

        record = collector.finish()

    assert record.tenant_id == "test_tenant"
    assert record.user_id == "user_007"
    assert len(record.root_span.children) == 1
    child = record.root_span.children[0]
    assert child.name == "orchestration.decide"
    assert len(child.children) == 1
    grandchild = child.children[0]
    assert grandchild.name == "agent.tool.search"
    assert grandchild.output_data["found"] == 3
    assert grandchild.duration_ms >= 0.0


def test_context_builder_anchors():
    """测试上下文构建器锚点 [doc_id:chunk_id] 生成与引用提取。"""
    builder = ContextBuilder(max_context_chars=1000)
    cands = [
        RetrievalCandidate(
            chunk=DocumentChunk(
                chunk_id="c1",
                doc_id="d1",
                doc_title="售后指引",
                content="支持 7 天无理由退换货。",
                line_start=5,
                line_end=8,
            ),
            score=0.92,
        ),
        RetrievalCandidate(
            chunk=DocumentChunk(
                chunk_id="c2",
                doc_id="d2",
                doc_title="物流时效",
                content="顺丰特快次日达。",
            ),
            score=0.85,
        ),
    ]

    context_text, citations = builder.build_context(cands)
    assert "[d1:c1]" in context_text
    assert "[d2:c2]" in context_text
    assert "《售后指引》" in context_text
    assert len(citations) == 2
    assert citations[0].doc_title == "售后指引"
    assert citations[0].chunk_id == "c1"


def test_base_tool_contract():
    """测试面向非人类消费者的受控工具基类契约（dry-run、有界截断与可操作错误）。"""
    class MockTool(BaseTool):
        name = "mock_tool"
        description = "Mock tool for testing"
        max_output_chars = 30

        def run(self, params, context):
            if params.get("trigger_error"):
                raise ValueError("参数不合法")
            return ToolResult(
                success=True,
                output="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            )

    tool = MockTool()

    # 1. 测试 dry-run
    dry_res = tool({"data": 1}, context=ToolContext(dry_run=True))
    assert dry_res.success
    assert dry_res.output["status"] == "dry_run_validated"

    # 2. 测试有界截断
    normal_res = tool({})
    assert normal_res.success
    assert normal_res.truncated
    assert len(normal_res.output) <= 50

    # 3. 测试错误引导 (Actionable Hint)
    err_res = tool({"trigger_error": True})
    assert not err_res.success
    assert "ValueError" in err_res.actionable_hint


def test_run_chat_minimal_loop():
    """测试阶段一最小闭环问答：检索 -> 组装引用 -> 生成 Trace 树。"""
    class MockRetriever:
        def retrieve(self, request):
            assert request.filter.tenant_id == "tenant_eval"
            assert request.filter.max_security_level == 1
            return [
                RetrievalCandidate(
                    chunk=DocumentChunk(
                        chunk_id="c_test",
                        doc_id="doc_faq",
                        doc_title="常见问题解答",
                        content="支持全国联保并享受原厂一年质保服务。",
                    ),
                    score=0.95,
                )
            ]

    tool = KnowledgeRetrievalTool(retriever=MockRetriever())
    req = ChatRequest(question="商品质保多久？")
    resp = run_chat_minimal_loop(
        req,
        tenant_id="tenant_eval",
        user_id="syn_staff1",
        security_level=1,
        retrieval_tool=tool,
    )

    assert isinstance(resp, ChatResponse)
    assert not resp.is_fallback
    assert len(resp.citations) >= 1
    assert resp.citations[0].doc_id == "doc_faq"
    assert resp.trace_id is not None
    assert resp.duration_ms >= 0.0