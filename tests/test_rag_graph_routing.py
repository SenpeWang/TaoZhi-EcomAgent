"""按实际 LangGraph 节点验证知识问答的补查、授权、证据与检查点。"""
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from taozhi.application.rag import intent, retrieval, specialists, workflow
from taozhi.application.rag.execution import _build_question_result
from taozhi.infrastructure.persistence.job_repo import conversation_question_reference
from taozhi.knowledge.db import database_connection


def _document(chunk_id="primary", *, allowed=True, score=0.1, rerank_score=None):
    return dict(kind="document", document_id=chunk_id, chunk_id=chunk_id, version=1, acl_version=1,
                tenant_id="tenant", title="规格表", quote="型号甲", level=1, ai_allowed=allowed,
                score=score, rerank_score=rerank_score, page=1)


@pytest.fixture
def rag(monkeypatch):
    """运行真实编译图和检查点，仅替换数据库、检索及模型依赖。"""
    job = dict(id="job", question="资料说明", input_level=1, payload={}, created_at=datetime.now(timezone.utc))
    principal = SimpleNamespace(id="owner", tenant_id="tenant", boss=False)
    for module in (intent, retrieval, specialists, workflow):
        monkeypatch.setattr(module, "validate_task_execution", lambda *_: (principal, job))
    decision = SimpleNamespace(selected=["product"], path=intent.DecisionPath.FAST_SEMANTIC)
    monkeypatch.setattr(intent, "get_decision_engine", lambda: SimpleNamespace(choice=lambda **_: decision))
    cfg = SimpleNamespace(adaptive_hyde=True, hyde_rerank_threshold=0.65)
    monkeypatch.setattr(retrieval, "load_config", lambda: cfg)
    monkeypatch.setattr(retrieval, "database_connection", nullcontext)
    monkeypatch.setattr(specialists.taozhi_config, "get_settings", lambda: SimpleNamespace(api_key=""))
    search = Mock(return_value=[_document()])
    hyde = Mock(return_value=None)
    monkeypatch.setattr(retrieval, "search_documents", search)
    monkeypatch.setattr(retrieval, "build_hyde_vector", hyde)
    monkeypatch.setattr(retrieval, "get_followup_context", lambda *_, **kw: ([], []))
    monkeypatch.setattr(retrieval, "extract_search_keyword", lambda query: query)

    def run():
        graph = workflow.build_rag_workflow(InMemorySaver())
        config = {"configurable": {"thread_id": "job:1"}}
        updates = list(graph.stream({"job_id": "job", "run_version": 1}, config, stream_mode="updates"))
        return {name: change for event in updates for name, change in event.items()}, graph.get_state(config).values

    return SimpleNamespace(job=job, search=search, hyde=hyde, decision=decision, config=cfg, run=run)


def test_sufficient_evidence_skips_retry_and_handles_missing_model(rag):
    updates, state = rag.run()
    assert list(updates) == ["planning", "retrieval", "prepare_evidence", "specialists", "quality", "publication"]
    assert state["retrieval_search_count"] == 1
    assert state["retrieval_decision"] == "run_specialists" and not state["hard_block"]
    assert _build_question_result(state)["model_calls"] == 0 and "尚未配置" in state["missing"][0]
    rag.hyde.assert_not_called()


@pytest.mark.parametrize("adaptive,score,rerank,reason", [
    (True, 0.1, 0.2, "retry_low_confidence"),
    (False, 0.01, 0.9, "retry_weak_evidence"),
])
def test_retry_merges_new_evidence_and_accounts_for_hyde_once(rag, monkeypatch, adaptive, score, rerank, reason):
    rag.config.adaptive_hyde = adaptive
    first = _document(score=score, rerank_score=rerank)
    hits = [[first], [first, _document("supplement")]]
    def search(*_, trace_out, **kwargs):
        trace_out.update(embed_ms=3, pg_ann_ms=4, hyde_ann_ms=1, es_fallback=True)
        return hits.pop(0)
    def hyde(*_, trace_out, **kwargs):
        trace_out.update(elapsed_ms=4, used=True, llm_attempts=1, total_tokens=11)
        return object()
    rag.search.side_effect = search
    rag.hyde.side_effect = hyde
    monkeypatch.setattr(retrieval, "extract_search_keyword", lambda query: "新词")
    updates, state = rag.run()
    assert updates["retrieval"]["retrieval_decision"] == reason
    assert list(updates).count("retrieval_retry") == 1 and state["retrieval_search_count"] == 2
    assert [doc["chunk_id"] for doc in state["evidence"]] == ["primary", "supplement"]
    assert state["timing"]["embed_ms"] == 6 and state["timing"]["pg_ann_ms"] == 10
    assert state["timing"]["hyde_ms"] == 4 and state["token_usage"]["total_tokens"] == 11
    assert state["degraded"]["es_fallback"] and state["degraded"]["adaptive_retry"]
    assert (rag.search.call_args_list[0].kwargs["hyde_vector"] is None) == adaptive
    assert (rag.search.call_args_list[1].kwargs["hyde_vector"] is not None) == adaptive
    rag.hyde.assert_called_once()


@pytest.mark.parametrize("has_evidence,allow_external,role,reason,hard,review", [
    (False, True, "product", "no_authorized_evidence", True, False),
    (False, False, "product", "no_authorized_evidence", True, False),
    (True, False, "product", "external_processing_blocked", False, True),
    (True, False, "finance", "no_finance_evidence", True, False),
    (True, True, "finance", "no_finance_evidence", True, False),
])
def test_no_model_routes_keep_review_and_publish_contract(rag, has_evidence, allow_external, role, reason, hard, review):
    rag.job["payload"].update(allow_external=allow_external, require_review=True)
    rag.search.return_value = [_document()] if has_evidence else []
    rag.decision.selected = [role]
    updates, state = rag.run()
    result = _build_question_result(state)
    assert "specialists" not in updates and "quality" not in updates
    assert result["hard_block"] == hard and state["review"] == review and result["model_calls"] == 0
    assert state["retrieval_decision"] == reason and result["missing"]
    if hard:
        assert "没有足够的授权资料" in result["answer"]
    else:
        assert state["evidence"][0]["quote"] in result["answer"] and "人工核验" in result["answer"]
    if not has_evidence:
        assert "retrieval_retry" in updates and state["retrieval_search_count"] == 1
        rag.search.assert_called_once()


def test_authorized_business_snapshot_is_evidence_with_no_documents(rag, monkeypatch):
    rag.job["payload"].update(sku_id="test-item", require_review=True)
    rag.search.return_value = []
    source = {"kind": "business", "level": 1}
    monkeypatch.setattr(retrieval, "lookup", lambda *_: {"available": True, "source": source, "record": {"available": 7}})
    updates, state = rag.run()
    assert "specialists" in updates and not state["hard_block"] and state["review"]
    assert state["sources"] == [source] and "可用库存：7" in state["answer"]


@pytest.mark.parametrize("allow_external,input_level", [(False, 1), (True, 3)])
def test_followup_pins_previous_question_and_keeps_its_external_policy(rag, monkeypatch, allow_external, input_level):
    rag.job["question"] = "它的规格呢"
    previous = dict(id="prior", question="合成内部提问", allow_external=allow_external, input_level=input_level)
    references = Mock(return_value=previous)
    monkeypatch.setattr(retrieval, "conversation_question_reference", references)
    rag.search.return_value = []
    _, state = rag.run()
    assert references.call_args_list[0].args[-1] is None and references.call_args_list[1].args[-1] == "prior"
    assert state["prior_question_id"] == "prior" and previous["question"] not in repr(state)
    assert previous["question"] in rag.search.call_args_list[0].args[2]
    rag.hyde.assert_not_called()


def test_retained_conversation_source_checks_external_permission(rag, monkeypatch):
    previous = {"kind": "metric", "metric_id": "internal", "tenant_id": "tenant"}
    monkeypatch.setattr(retrieval, "get_followup_context", lambda *_, **kw: ([], [previous]))
    check = Mock(return_value=False)
    monkeypatch.setattr(retrieval, "sources_valid", check)
    updates, state = rag.run()
    assert state["retrieval_decision"] == "external_processing_blocked" and "specialists" not in updates
    assert previous in state["sources"] and check.call_args.args[2] == [previous]
    assert check.call_args.kwargs == {"external": True}


def test_postgres_previous_question_respects_owner_tenant_conversation_and_order():
    """实际 PostgreSQL 排除跨范围、尚未完成及晚于本轮的提问。"""
    before = (datetime(2026, 1, 2, tzinfo=timezone.utc), "current")
    with database_connection() as conn:
        conn.execute("CREATE TEMP TABLE jobs(id text,tenant_id text,owner_id text,conversation_id text,created_at timestamptz,"
                     "kind text,state text,question text,input_level int,payload jsonb) ON COMMIT DROP")
        for identifier, tenant, owner, conversation, time, status in (
            ("older", "t", "u", "c", "2026-01-01", "completed"),
            ("before", "t", "u", "c", "2026-01-02", "completed"),
            ("later", "t", "u", "c", "2026-01-02", "completed"),
            ("future", "t", "u", "c", "2026-01-03", "completed"),
            ("wrong_tenant", "other", "u", "c", "2026-01-01", "completed"),
            ("wrong_owner", "t", "other", "c", "2026-01-01", "completed"),
            ("wrong_conversation", "t", "u", "other", "2026-01-01", "completed"),
            ("unfinished", "t", "u", "c", "2026-01-01", "running"),
        ):
            conn.execute("INSERT INTO jobs VALUES(%s,%s,%s,%s,%s,'ask',%s,'合成问题',2,'{\"allow_external\":false}')",
                         (identifier, tenant, owner, conversation, time, status))
        row = conversation_question_reference(conn, "t", "u", "c", before)
        assert row["id"] == "before" and row["allow_external"] is False
        assert conversation_question_reference(conn, "t", "u", "c", before, "older")["id"] == "older"
        for reference in ("later", "future", "wrong_tenant", "wrong_owner", "wrong_conversation", "unfinished"):
            assert conversation_question_reference(conn, "t", "u", "c", before, reference) is None
