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
    rows = []
    monkeypatch.setattr(tracing, "submit_span", rows.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")

    def tool(parent_context):
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
    from taozhi.core.tools import outcome

    transaction = ContextVar("test.transaction", default=None)
    rows = []
    observed = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", rows.append)

    def failed_tool():
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


def test_running_and_failed_snapshots_only_store_operational_attributes(monkeypatch):
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


def test_retrieval_model_spans_only_measure_real_requests(monkeypatch):
    from types import SimpleNamespace

    from taozhi.knowledge import embedding

    snapshots = []
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    monkeypatch.setattr(tracing, "submit_span", snapshots.append)
    monkeypatch.setattr(embedding, "load_config", lambda: SimpleNamespace(embed_service_url="http://local", rerank_timeout=5))
    monkeypatch.setattr(embedding, "model_signature", lambda: "encoder:2")
    monkeypatch.setattr(embedding, "_query_cache", embedding.OrderedDict())
    monkeypatch.setattr(embedding, "_query_stats", {"hits": 0, "misses": 0})
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
    events = queue.Queue(66)
    monkeypatch.setattr(span_writer, "_CAPACITY", 66)
    monkeypatch.setattr(span_writer, "_QUEUE", events)
    monkeypatch.setattr(span_writer, "_thread", Mock())
    for name in ("a", "b", "c"):
        span_writer.submit_span({"name": name, "parent_span_id": "parent"})
    span_writer.submit_span({"name": "root", "parent_span_id": None})
    assert [events.get_nowait()["name"] for _ in range(events.qsize())] == ["a", "b", "root"]


def test_batch_retry_and_graceful_flush(monkeypatch):
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
