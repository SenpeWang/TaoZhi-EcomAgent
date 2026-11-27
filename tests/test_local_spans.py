"""内建 OpenTelemetry 采集器的隔离、失败与队列边界。"""
import queue
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from unittest.mock import Mock

import pytest
from openinference.semconv.trace import SpanAttributes
from opentelemetry import context, trace
from opentelemetry.trace import StatusCode

from taozhi.observability import span_writer, tracing


def test_attempt_context_crosses_selected_threads_without_crossing_jobs(monkeypatch):
    """验证追踪上下文可跨指定线程传播，且不同任务与重试之间互不串线。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    rows = []
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")

    def tool(parent_context):
        """在给定父上下文中开启工具 span 并返回当前追踪 ID。

        Args:
            parent_context: 父级追踪上下文。
        """
        with tracing.node_span("tool.search", "TOOL", context=parent_context):
            return tracing.current_trace_id()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with tracing.job_trace("tenant_a", "a", 1, "task.ask"):
            first_id = tracing.current_trace_id()
            with tracing.node_span("agent.product", "AGENT"):
                assert pool.submit(tool, context.get_current()).result() == first_id
                assert pool.submit(tracing.current_trace_id).result() == ""
        with tracing.job_trace("tenant_a", "a", 2, "task.ask"):
            retry_id = tracing.current_trace_id()
        with tracing.job_trace("tenant_b", "b", 1, "task.ask"):
            other_id = tracing.current_trace_id()
        with tracing.node_span("standalone.eval", "CHAIN"):
            assert tracing.current_trace_id()

    assert len({first_id, retry_id, other_id}) == 3
    ended = {r["name"]: r for r in rows if r["trace_id"] == first_id and r["status"] == "ok"}
    assert ended["task.ask"]["parent_span_id"] is None
    assert ended["agent.product"]["parent_span_id"] == ended["task.ask"]["span_id"]
    assert ended["tool.search"]["parent_span_id"] == ended["agent.product"]["span_id"]
    assert all(r["run_version"] == 2 for r in rows if r["trace_id"] == retry_id)
    assert all(r["tenant_id"] == "tenant_b" and r["job_id"] == "b" for r in rows if r["trace_id"] == other_id)
    assert not any(r["name"] == "standalone.eval" for r in rows)


def test_tool_pool_propagates_only_the_trace_and_cleans_it_on_error(monkeypatch):
    """验证工具线程池只传播追踪上下文，并在工具异常后清理。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    from taozhi.core.tools import outcome

    transaction = ContextVar("test.transaction", default=None)
    rows = []
    observed = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", rows.append)

    def failed_tool():
        """记录工具线程上下文后抛出含私密正文的异常。"""
        observed.append((tracing.current_trace_id(), transaction.get()))
        raise ValueError("private-content")

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(outcome, "_get_tool_pool", lambda: pool)
        with tracing.job_trace("tenant", "job", 1, "task.research"):
            job_trace_id = tracing.current_trace_id()
            token = transaction.set("connection-on-calling-thread")
            try:
                result = outcome.execute_tool_with_outcome("search", failed_tool, {}, remaining_seconds=2)
            finally:
                transaction.reset(token)
        assert result.status == "error"
        assert observed == [(job_trace_id, None)]
        assert pool.submit(tracing.current_trace_id).result() == ""


def test_retrieval_routes_export_only_declared_decisions_and_counts(monkeypatch):
    """验证检索路由只导出已声明的决策与计数属性，不泄漏查询原文。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    from taozhi.application.common.task_guard import traced_node
    from taozhi.application.services.trace_service import _KEY_ACTIVITY_STEPS, _span_display_name
    from taozhi.domain.trace_activity import activity_category

    rows = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    with tracing.job_trace("tenant", "job", 1, "task.ask"):
        for name, decision, count in (
            ("retrieve_documents", "retry_low_confidence", 1),
            ("refine_query", "private-query", 2),
            ("prepare_context", "external_processing_blocked", 2),
        ):
            traced_node(name, dict.copy)({"retrieval_decision": decision, "retrieval_search_count": count})
        traced_node("extract_claims", dict.copy)({"verification_issue": "model_unavailable"})
        traced_node("compose_answer", dict.copy)({"answer_basis": "source_excerpt", "verification_issue": "private-query", "phase": "answer_composed"})

    ended = {r["name"]: r["attributes"] for r in rows if r["status"] == "ok"}
    assert ended["pipeline.retrieve_documents"]["rag.retrieval_decision"] == "retry_low_confidence"
    assert ended["pipeline.refine_query"]["rag.retrieval_search_count"] == 2
    assert ended["pipeline.prepare_context"]["rag.retrieval_decision"] == "external_processing_blocked"
    assert ended["pipeline.extract_claims"]["rag.verification_issue"] == "model_unavailable"
    assert ended["pipeline.compose_answer"]["rag.answer_basis"] == "source_excerpt"
    assert ended["pipeline.compose_answer"]["rag.phase"] == "answer_composed"
    assert "private-query" not in str(rows)
    assert set(ended) - {"task.ask"} <= set(_KEY_ACTIVITY_STEPS)
    assert _span_display_name("CHAIN", "pipeline.refine_query", {}) == "尝试补查授权文档"
    assert activity_category("CHAIN", "pipeline.prepare_context") == "CONTEXT"


def test_running_and_failed_snapshots_only_store_operational_attributes(monkeypatch):
    """验证运行中与失败快照只保存运维属性，失败时记录异常类型。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    rows = []
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    with pytest.raises(ValueError, match="private-content"):
        with tracing.job_trace("tenant", "job", 1, "task.research"):
            attributes = {
                SpanAttributes.LLM_MODEL_NAME: "catalog-model",
                SpanAttributes.INPUT_VALUE: "private-content",
            }
            with tracing.node_span("llm.chat", "LLM", attributes) as span:
                assert trace.get_current_span() is span
                assert rows[-1]["kind"] == "LLM" and rows[-1]["status"] == "running"
                assert rows[-1]["attributes"][SpanAttributes.LLM_MODEL_NAME] == "catalog-model"
                assert span.attributes[SpanAttributes.LLM_MODEL_NAME] == "catalog-model"
                span.set_input("private-content")
                span.set_output("private-content")
                assert "private-content" not in str(span.attributes)
                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, 13)
                raise ValueError("private-content")

    llm = next(r for r in rows if r["kind"] == "LLM" and r["status"] == "error")
    assert llm["error_type"] == "ValueError" and llm["duration_ms"] >= 0
    assert llm["ended_at"] >= llm["started_at"]
    assert llm["attributes"][SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 13
    assert "private-content" not in str(rows)
    assert rows[-1]["status"] == "error" and tracing.current_trace_id() == ""

    with tracing.job_trace("tenant", "job", 2, "task.ask") as span:
        span.set_status(StatusCode.ERROR)
    assert rows[-1]["status"] == "error"


def test_workflow_agent_and_tool_names_use_inference_semantics(monkeypatch):
    """验证工作流、智能体与工具 span 使用推理语义命名并保持父子关系。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    snapshots = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", snapshots.append)
    with tracing.job_trace("tenant", "research-job", 1, "task.research"):
        with tracing.node_span("research.market", "AGENT", {SpanAttributes.AGENT_NAME: "market"}) as agent:
            assert agent.attributes[SpanAttributes.AGENT_NAME] == "market"
            with tracing.node_span("mcp.call_tool", "TOOL", {SpanAttributes.TOOL_NAME: "search"}) as tool:
                assert tool.attributes[SpanAttributes.TOOL_NAME] == "search"

    completed = {row["kind"]: row for row in snapshots if row["status"] == "ok"}
    assert completed["TOOL"]["parent_span_id"] == completed["AGENT"]["span_id"]
    assert completed["AGENT"]["attributes"][SpanAttributes.AGENT_NAME] == "market"
    assert completed["TOOL"]["attributes"][SpanAttributes.TOOL_NAME] == "search"


def test_live_event_requires_the_scope_bound_to_its_span(monkeypatch):
    """跨租户拼接的上下文既不能创建实时快照，也不能污染原始节点。"""
    rows = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    with tracing.job_trace("first", "job1", 1, "task.ask"):
        wrong = context.attach(context.set_value(tracing._JOB, ("second", "job2", 1)))
        try:
            tracing.record_diagnostic("model.rate_limit", "retry")
        finally:
            context.detach(wrong)
        tracing.record_diagnostic("context.prepare", "fallback")
    assert {r["tenant_id"] for r in rows} == {"first"}
    assert [e["code"] for e in rows[-1]["diagnostics"]] == ["context.prepare"]


def test_retrieval_model_spans_only_measure_real_requests(monkeypatch):
    """验证检索与模型 span 只统计真实请求，缓存命中或超时不伪造时延。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    from types import SimpleNamespace

    from taozhi.infrastructure.knowledge import embedding

    snapshots = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", snapshots.append)
    monkeypatch.setattr(embedding, "load_config", lambda: SimpleNamespace(embed_service_url="http://local", rerank_timeout=5))
    monkeypatch.setattr(embedding, "model_signature", lambda: "encoder:2")
    monkeypatch.setattr(embedding, "_query_cache", embedding.OrderedDict())
    post = Mock(side_effect=[
        Mock(json=lambda: {"model": "encoder", "vectors": [[0.1, 0.2]]}),
        Mock(json=lambda: {"model": "reranker", "scores": [0.95]}),
        TimeoutError("private-content"),
    ])
    monkeypatch.setattr(embedding.httpx, "post", post)
    with tracing.job_trace("tenant", "job", 1, "task.ask"):
        with tracing.node_span("knowledge.search", "RETRIEVER") as search:
            embedding.encode_query("private-content")
            embedding.encode_query("private-content")
            assert embedding.rerank("private-content", ["private-content"]) == [0.95]
            with pytest.raises(TimeoutError):
                embedding.encode_passages(["private-content"])
    children = [s for s in snapshots if s["ended_at"] and s["parent_span_id"] == trace.format_span_id(search.get_span_context().span_id)]
    assert [(s["kind"], s["name"], s["status"]) for s in children] == [
        ("EMBEDDING", "embedding.query", "ok"), ("RERANKER", "reranker.rerank", "ok"), ("EMBEDDING", "embedding.passages", "error"),
    ]
    assert children[0]["attributes"]["embedding.model_name"] == "encoder"
    assert children[1]["attributes"]["reranker.model_name"] == "reranker"
    assert children[2]["error_type"] == "TimeoutError"
    assert post.call_count == 3 and "private-content" not in str(snapshots)


def test_disabled_tracing_and_telemetry_failure_preserve_job_behavior(monkeypatch):
    """验证关闭追踪或上报失败时任务行为不变，且不吞掉业务异常。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    submit = Mock(side_effect=RuntimeError("sink unavailable"))
    monkeypatch.setattr(tracing, "submit_span", submit)
    monkeypatch.setenv("TAOZHI_TRACING", "0")
    with tracing.job_trace("tenant", "job", 1, "task.ask") as span:
        assert span is None and tracing.current_trace_id() == ""
    submit.assert_not_called()
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    with pytest.raises(ValueError, match="original"):
        with tracing.job_trace("tenant", "job", 1, "task.ask"):
            raise ValueError("original")
    assert submit.call_count == 2


def test_writer_reserves_high_water_capacity_for_roots(monkeypatch):
    """验证写入器为根 span 预留高水位容量，避免根事件被丢弃。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    events = queue.Queue(66)
    monkeypatch.setattr(span_writer, "_CAPACITY", 66)
    monkeypatch.setattr(span_writer, "_QUEUE", events)
    monkeypatch.setattr(span_writer, "_thread", Mock())
    for name in ("a", "b", "c"):
        span_writer.submit_span({"name": name, "parent_span_id": "parent"})
    span_writer.submit_span({"name": "root", "parent_span_id": None})
    assert [events.get_nowait()["name"] for _ in range(events.qsize())] == ["a", "b", "root"]


def test_batch_retry_and_graceful_flush(monkeypatch):
    """验证批量写入失败后重试，并能在退出前优雅刷盘。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    from taozhi.infrastructure.persistence import span_repo

    write = Mock(side_effect=[OSError("temporary"), None])
    monkeypatch.setattr(span_repo, "write_spans", write)
    row = {"name": "task.ask", "parent_span_id": None}
    span_writer.submit_span(row)
    assert span_writer.flush_spans(3)
    assert [call.args[0] for call in write.call_args_list] == [[row], [row]]


def test_native_sdk_child_keeps_start_tenant_when_ended_in_another_job(monkeypatch):
    """第三方通用 OTel 埋点从 Worker 的全局 provider 读取上下文，错时结束也归原始租户。"""
    providers = [trace.ProxyTracerProvider()]
    rows = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: providers[-1])
    monkeypatch.setattr(trace, "set_tracer_provider", providers.append)
    assert tracing.setup_tracing()
    assert tracing.setup_tracing()
    assert len(providers) == 2
    library = trace.get_tracer("vendor.instrumentation")
    unbound = library.start_span("not-a-task")
    with tracing.job_trace("first", "job1", 1, "task.research") as root:
        native = library.start_span("request-value-secret")
        native.set_attribute("http.url", "request-value-secret")
        native.set_status(StatusCode.ERROR, "request-value-secret")
        root_id = trace.format_span_id(root.get_span_context().span_id)
    with tracing.job_trace("second", "job2", 1, "task.ask"):
        native.end()
        unbound.end()
    emitted = [r for r in rows if r["name"] == "vendor.instrumentation.internal"]
    assert [r["status"] for r in emitted] == ["running", "error"]
    assert all(r["tenant_id"] == "first" and r["job_id"] == "job1" and r["parent_span_id"] == root_id for r in emitted)
    assert "request-value-secret" not in str(rows)


def test_live_diagnostics_keep_multiple_failures_private_and_attributed(monkeypatch, caplog):
    """同一工具的降级与失败在结束前可见，异常传播不在父步骤重复登记。"""
    rows = []
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    with pytest.raises(ValueError, match="private-payload"):
        with tracing.job_trace("tenant_a", "job", 2, "task.research"):
            with tracing.node_span("research.subagent_worker", "AGENT"):
                with tracing.node_span("mcp.call_tool", "TOOL") as span:
                    tracing.record_diagnostic("tool.gateway", "fallback", TypeError("private-http-url"), http_status=503)
                    assert rows[-1]["status"] == "running" and rows[-1]["diagnostics"][0]["http_status"] == 503
                    span.add_event("exception", {"exception.message": "private-payload"})
                    raise ValueError("private-payload")
    tool = next(row for row in rows if row["kind"] == "TOOL" and row["ended_at"])
    assert [(d["code"], d["level"], d["exception_type"]) for d in tool["diagnostics"]] == [
        ("tool.gateway", "warning", "TypeError"), ("tool.error", "error", "ValueError"),
    ]
    assert all(row["diagnostics"] == [] for row in rows if row["kind"] != "TOOL")
    assert all(row["tenant_id"] == "tenant_a" and row["run_version"] == 2 for row in rows)
    assert caplog.records[-1].trace_id == tool["trace_id"] and caplog.records[-1].span_id == tool["span_id"]
    assert "private-" not in str(rows) and "private-" not in caplog.text


def test_failed_scraper_is_not_reported_as_an_empty_search(monkeypatch, tmp_path):
    """走真实 MCP 调用线程，验证网关故障、缺失抓手和合法空结果有各自的状态。"""
    import importlib
    import httpx
    from taozhi.infrastructure.mcp.adapter import McpClientAdapter
    from taozhi.infrastructure.mcp.server import EcommerceMcpServer

    search = importlib.import_module("taozhi.core.tools.search_1688")
    rows = []
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(search, "resolve_runtime_dir", lambda: tmp_path)
    monkeypatch.setenv("ECOM_RESEARCH_P1688_URL", "https://example.test/gateway")
    responses = iter([httpx.Response(502), httpx.Response(200, json={"items": []})])
    def respond(transport, request):
        """按预设响应序列返回 httpx 响应并绑定请求对象。

        Args:
            transport: 被忽略的传输层对象。
            request: httpx 请求对象。
        """
        response = next(responses)
        response.request = request
        return response
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    adapter = McpClientAdapter(server=EcommerceMcpServer())
    with tracing.job_trace("t", "j", 1, "task.research"):
        with tracing.node_span("research.subagent_worker", "AGENT") as agent:
            failed = adapter.call_tool("search_1688_wholesale", {"keyword": "private-keyword"})
            empty = adapter.call_tool("search_1688_wholesale", {"keyword": "private-keyword"})
    assert (failed.status, empty.status) == ("error", "empty")
    tools = [row for row in rows if row["kind"] == "TOOL" and row["ended_at"]]
    assert [row["parent_span_id"] for row in tools] == [trace.format_span_id(agent.get_span_context().span_id)] * 2
    assert [e["code"] for e in tools[0]["diagnostics"]] == ["tool.gateway", "tool.scraper_missing"]
    assert tools[0]["diagnostics"][0]["http_status"] == 502 and tools[1]["diagnostics"] == []
    assert "private-keyword" not in str(rows)


def test_observed_rates_include_failures_and_empty_searches():
    """进程指标按真实结束的调用计算，只有失败时错误率也在零到一之间。"""
    from taozhi.ai import observability

    observability.reset()
    try:
        with observability.usage_scope() as usage:
            observability.record_llm_error("fast")
            observability.record_llm_error("fast")
            observability.record_llm_call("fast", 0.1, 3, 2)
            observability.record_retrieval(0)
            observability.record_retrieval(4)
        counters = observability.snapshot()
        assert counters["llm.error_rate"] == pytest.approx(2 / 3)
        assert counters["rag.hit_rate"] == 0.5
        assert usage.llm_attempts == 3 and usage.llm_errors == 2
    finally:
        observability.reset()
