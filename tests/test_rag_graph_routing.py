"""按实际 LangGraph 节点验证知识问答的补查、授权、证据与检查点。"""
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from taozhi.application.rag import intent, publication, retrieval, specialists, workflow
from taozhi.application.rag.execution import _build_question_result
from taozhi.core.memory import service as memory_service
from taozhi.infrastructure.persistence.job_repo import conversation_question_reference
from taozhi.infrastructure.persistence.database import database_connection


def _document(chunk_id="primary", *, allowed=True, score=0.1, rerank_score=None):
    """构造检索命中文档字典。

    Args:
        chunk_id: 切片标识。
        allowed: 是否允许 AI 使用。
        score: 融合检索分数。
        rerank_score: 精排分数。
    """
    return dict(kind="document", document_id=chunk_id, chunk_id=chunk_id, version=1, acl_version=1,
                tenant_id="tenant", title="规格表", quote="型号甲", level=1, ai_allowed=allowed,
                score=score, rerank_score=rerank_score, page=1)


@pytest.fixture
def rag(monkeypatch):
    """运行真实编译图和检查点，仅替换数据库、检索及模型依赖。"""
    job = dict(id="job", question="资料说明", input_level=1, payload={}, created_at=datetime.now(timezone.utc))
    principal = SimpleNamespace(id="owner", tenant_id="tenant", boss=False)
    for module in (intent, retrieval, specialists, publication):
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
        """运行真实 RAG 编译图并收集状态更新。"""
        graph = workflow.build_rag_workflow(InMemorySaver())
        config = {"configurable": {"thread_id": "job:1"}}
        updates = list(graph.stream({"job_id": "job", "run_version": 1}, config, stream_mode="updates"))
        return {name: change for event in updates for name, change in event.items()}, graph.get_state(config).values

    return SimpleNamespace(job=job, search=search, hyde=hyde, decision=decision, config=cfg, run=run)


def test_sufficient_evidence_skips_retry_and_handles_missing_model(rag):
    """验证证据充分时跳过补查，并在模型未配置时如实标注缺失。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
    """
    updates, state = rag.run()
    assert list(updates) == ["analyze_question", "retrieve_documents", "prepare_context", "extract_claims", "verify_claims", "compose_answer"]
    assert [change["phase"] for change in updates.values()] == [
        "question_analyzed", "documents_retrieved", "context_prepared",
        "claims_extracted", "claims_verified", "answer_composed",
    ]
    assert state["retrieval_search_count"] == 1
    assert state["retrieval_decision"] == "run_specialists" and not state["hard_block"]
    assert _build_question_result(state)["model_calls"] == 0 and "尚未配置" in state["missing"][0]
    rag.hyde.assert_not_called()


@pytest.mark.parametrize("question,allow,basis", [
    ("什么是 USB PD？", True, "public_reference"),
    ("什么是 USB PD？", False, "insufficient_evidence"),
    ("什么是 USB PD，本店这款能保证快充吗？", True, "insufficient_evidence"),
    ("帮我上网看看本店保修多久？", True, "insufficient_evidence"),
])
def test_empty_knowledge_base_keeps_public_definitions_separate(rag, question, allow, basis):
    """验证空知识库下公开定义与内部结论分离，硬阻断状态正确。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        question: ``question`` 参数化取值。
        allow: ``allow`` 参数化取值。
        basis: ``basis`` 参数化取值。
    """
    rag.job.update(question=question, payload={"allow_public_reference": allow, "require_review": True})
    rag.search.return_value = []
    updates, state = rag.run()
    result = _build_question_result(state)
    assert "extract_claims" not in updates and result["answer_basis"] == basis
    assert result["guidance"] and not result["citations"]
    assert bool(result["public_sources"]) == (basis == "public_reference")
    assert state["review"] == (basis == "public_reference")
    assert state["hard_block"] == (basis == "insufficient_evidence")


def test_live_values_require_the_matching_business_field(rag, monkeypatch):
    """验证实时库存与价格只回答对应业务字段，未授权字段列入缺失。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    rag.job.update(question="这个商品的实时库存和当前售价？", payload={"sku_id": "SKU-1"})
    source = {"kind": "business", "level": 1}
    record = {"sku_id": "SKU-1", "available": 0, "purchase_cost_minor": 120, "as_of": "2026-01-01"}
    monkeypatch.setattr(retrieval, "lookup", lambda *_: {"available": True, "source": source, "record": record})
    updates, state = rag.run()
    result = _build_question_result(state)
    assert list(updates) == ["analyze_question", "fetch_live_data", "compose_answer"]
    assert [change["phase"] for change in updates.values()] == ["question_analyzed", "live_data_loaded", "answer_composed"]
    assert result["answer_basis"] == "business_snapshot" and "可用库存：0" in result["answer"]
    assert "采购成本" not in result["answer"] and "销售价格" in result["missing"][0]
    assert not result["citations"] and state["sources"] == [source]
    rag.search.assert_not_called()


def test_current_order_status_without_identifier_never_uses_documents(rag):
    """验证查询订单状态缺少编号时直接要求单号，不检索文档。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
    """
    rag.job.update(question="帮我查订单状态", payload={"allow_public_reference": True})
    _, state = rag.run()
    assert state["answer_basis"] == "insufficient_evidence" and "订单编号" in state["answer"]
    assert not state["public_sources"] and not state["findings"]
    rag.search.assert_not_called()


@pytest.mark.parametrize("question,reason", [
    ("昨天这款商品售价是多少？", "指定时点"),
    ("昨天订单发货了吗？", "指定时点"),
    ("明天有货吗？", "指定时点"),
    ("昨天这件多少钱？", "指定时点"),
    ("预计下周库存多少？", "指定时点"),
    ("什么时候有现货？", "指定时点"),
    ("这款商品的供应商报价为多少，采购价格是多少？", "报价单"),
])
def test_current_snapshot_cannot_replace_historical_forecast_or_special_price(rag, monkeypatch, question, reason):
    """验证当前业务快照不能替代历史、预测或报价类问题。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        question: ``question`` 参数化取值。
        reason: ``reason`` 参数化取值。
    """
    rag.job.update(question=question, payload={"sku_id": "SKU-1", "allow_public_reference": True})
    lookup = Mock()
    monkeypatch.setattr(retrieval, "lookup", lookup)
    updates, state = rag.run()
    assert list(updates) == ["analyze_question", "fetch_live_data", "compose_answer"]
    assert [change["phase"] for change in updates.values()] == ["question_analyzed", "live_data_loaded", "answer_composed"]
    assert state["answer_basis"] == "insufficient_evidence" and state["hard_block"]
    assert any(reason in item for item in state["missing"])
    assert not state["sources"] and not state["public_sources"]
    lookup.assert_not_called()
    rag.search.assert_not_called()


@pytest.mark.parametrize("policy,missing", [
    ("可以退货吗、保修多久？", ["退换与退款", "保修条件"]),
    ("售后政策如何？", ["售后政策"]),
])
def test_live_price_reports_unanswered_policy_separately(rag, monkeypatch, policy, missing):
    """验证实时价格回答单独标注未覆盖的售后政策问题。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        policy: ``policy`` 参数化取值。
        missing: ``missing`` 参数化取值。
    """
    rag.job.update(question=f"这款商品现在卖多少钱？{policy}", payload={"sku_id": "SKU-1"})
    record = {"sku_id": "SKU-1", "price_minor": 0, "currency": "CNY", "available": 55}
    snapshot = {"available": True, "record": record, "source": {"kind": "business", "level": 1}}
    monkeypatch.setattr(retrieval, "lookup", lambda *_: snapshot)
    updates, state = rag.run()
    assert list(updates) == ["analyze_question", "fetch_live_data", "compose_answer"]
    assert [change["phase"] for change in updates.values()] == ["question_analyzed", "live_data_loaded", "answer_composed"]
    assert state["answer_basis"] == "business_snapshot" and "销售价格（分）：0" in state["answer"]
    assert "55" not in state["answer"]
    assert all(any(subject in gap for gap in state["missing"]) for subject in missing)


@pytest.mark.parametrize("question,record", [
    ("资料说明", {"sku_id": "SKU-1", "as_of": "2026-01-01"}),
    ("这个商品的保修条件具体有哪些？", {"available": 5}),
])
def test_snapshot_without_responsive_fields_is_not_an_answer(rag, monkeypatch, question, record):
    """验证快照缺少可回应字段时不当作答案，也不查询业务数据。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        question: ``question`` 参数化取值。
        record: ``record`` 参数化取值。
    """
    rag.job.update(question=question, payload={"sku_id": "SKU-1"})
    rag.search.return_value = []
    snapshot = {"available": True, "record": record, "source": {"kind": "business", "level": 1}}
    lookup = Mock(return_value=snapshot)
    monkeypatch.setattr(retrieval, "lookup", lookup)
    _, state = rag.run()
    assert state["answer_basis"] == "insufficient_evidence" and "实时业务数据" not in state["answer"]
    if "保修" in question:
        lookup.assert_not_called()


def test_manual_excerpt_retains_late_eligibility_conditions(rag):
    """验证人工原文摘录保留后置的适用条件。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
    """
    rag.job.update(question="保修条件是什么？", payload={"allow_external": False})
    text = "保修规定。" + "请核对购买凭证。" * 72 + "仅限直营渠道，第三方店铺除外。"
    rag.search.return_value = [{**_document(), "quote": text}]
    _, state = rag.run()
    assert state["answer_basis"] == "source_excerpt" and text in state["answer"]


@pytest.mark.parametrize("adaptive,score,rerank,reason", [
    (True, 0.1, 0.2, "retry_low_confidence"),
    (False, 0.01, 0.9, "retry_weak_evidence"),
])
def test_retry_merges_new_evidence_and_accounts_for_hyde_once(rag, monkeypatch, adaptive, score, rerank, reason):
    """验证低置信补查合并新证据，且 HyDE 只计一次用量。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        adaptive: ``adaptive`` 参数化取值。
        score: ``score`` 参数化取值。
        rerank: ``rerank`` 参数化取值。
        reason: ``reason`` 参数化取值。
    """
    rag.config.adaptive_hyde = adaptive
    first = _document(score=score, rerank_score=rerank)
    hits = [[first], [first, _document("supplement")]]
    def search(*_, trace_out, **kwargs):
        """替身检索函数，返回预设命中并写入检索计时。

        Args:
            _: 被忽略的位置参数。
            kwargs: 关键字参数。
            trace_out: 追踪计时输出字典。
        """
        trace_out.update(embed_ms=3, pg_ann_ms=4, hyde_ann_ms=1, es_fallback=True)
        return hits.pop(0)
    def hyde(*_, trace_out, **kwargs):
        """替身 HyDE 函数，写入改写耗时与 token 用量。

        Args:
            _: 被忽略的位置参数。
            kwargs: 关键字参数。
            trace_out: 追踪计时输出字典。
        """
        trace_out.update(elapsed_ms=4, used=True, llm_attempts=1, total_tokens=11)
        return object()
    rag.search.side_effect = search
    rag.hyde.side_effect = hyde
    monkeypatch.setattr(retrieval, "extract_search_keyword", lambda query: "新词")
    updates, state = rag.run()
    assert updates["retrieve_documents"]["retrieval_decision"] == reason
    assert list(updates).count("refine_query") == 1 and state["retrieval_search_count"] == 2
    assert [change["phase"] for change in updates.values()] == [
        "question_analyzed", "documents_retrieved", "query_refined", "context_prepared",
        "claims_extracted", "claims_verified", "answer_composed",
    ]
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
    """验证无模型路由仍保持复核、硬阻断与发布契约。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        has_evidence: ``has_evidence`` 参数化取值。
        allow_external: ``allow_external`` 参数化取值。
        role: ``role`` 参数化取值。
        reason: ``reason`` 参数化取值。
        hard: ``hard`` 参数化取值。
        review: ``review`` 参数化取值。
    """
    rag.job["question"] = "商品背板颜色是什么？"
    rag.job["payload"].update(allow_external=allow_external, require_review=True)
    rag.search.return_value = [{**_document(), "quote": "商品背板颜色为黑色。"}] if has_evidence else []
    rag.decision.selected = [role]
    updates, state = rag.run()
    result = _build_question_result(state)
    assert "extract_claims" not in updates and "verify_claims" not in updates
    assert result["hard_block"] == hard and state["review"] == review and result["model_calls"] == 0
    assert state["retrieval_decision"] == reason and result["missing"]
    if hard:
        assert "没有足够的授权资料" in result["answer"]
    else:
        assert state["evidence"][0]["quote"] in result["answer"] and "人工核验" in result["answer"]
    if not has_evidence:
        assert "refine_query" in updates and state["retrieval_search_count"] == 1
        rag.search.assert_called_once()


@pytest.mark.parametrize("allow_external", [True, False])
def test_model_free_graph_shows_only_applicable_manual_excerpts(rag, allow_external):
    """验证无模型图只展示适用的人工原文摘录并正确引用。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        allow_external: ``allow_external`` 参数化取值。
    """
    rag.job.update(question="MC-500 的切幅是多少？", payload={"allow_external": allow_external, "require_review": True})
    rag.search.return_value = [
        {**_document("other-model"), "quote": "MC-501 切幅为 220mm。"},
        {**_document("right-model"), "quote": "MC-500 切幅为 120mm，仅适用于合成批次。"},
    ]
    updates, state = rag.run()
    result = _build_question_result(state)
    assert ("extract_claims" in updates) == allow_external
    assert state["review"] and result["answer_basis"] == "source_excerpt" and not result["public_sources"]
    assert result["model_calls"] == 0 and not state["findings"] and not result["cache_hit"]
    assert [citation["cited"] for citation in result["citations"]] == [False, True]
    assert state["evidence"][1]["quote"] + " [2]" in result["answer"] and "220mm" not in result["answer"]


def test_authorized_business_snapshot_is_evidence_with_no_documents(rag, monkeypatch):
    """验证授权业务快照可作为证据，无需文档且不泄漏敏感字段。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    rag.job["payload"].update(sku_id="test-item", require_review=True)
    rag.search.return_value = []
    source = {"kind": "business", "level": 1}
    record = {"available": 7, "phone": "保密电话", "address": "保密地址", "purchase_cost_minor": 941}
    monkeypatch.setattr(retrieval, "lookup", lambda *_: {"available": True, "source": source, "record": record})
    updates, state = rag.run()
    assert "extract_claims" in updates and not state["hard_block"] and state["review"]
    assert state["sources"] == [source] and "可用库存：7" in state["answer"]
    assert state["business"][0]["record"] == {"available": 7}
    assert "保密" not in state["answer"] and "941" not in state["answer"]


@pytest.mark.parametrize("allow_external,input_level", [(False, 1), (True, 3)])
def test_followup_pins_previous_question_and_keeps_its_external_policy(rag, monkeypatch, allow_external, input_level):
    """验证追问锁定上一问并沿用其外部处理策略。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        allow_external: ``allow_external`` 参数化取值。
        input_level: ``input_level`` 参数化取值。
    """
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
    """验证沿用会话来源前重新校验外部处理权限。

    Args:
        rag: 运行真实编译图与检查点、仅替换数据库/检索/模型依赖的 RAG 工作流夹具。
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    previous = {"kind": "metric", "metric_id": "internal", "tenant_id": "tenant"}
    monkeypatch.setattr(retrieval, "get_followup_context", lambda *_, **kw: ([], [previous]))
    check = Mock(return_value=False)
    monkeypatch.setattr(retrieval, "sources_valid", check)
    updates, state = rag.run()
    assert state["retrieval_decision"] == "external_processing_blocked" and "extract_claims" not in updates
    assert previous in state["sources"] and check.call_args.args[2] == [previous]
    assert check.call_args.kwargs == {"external": True}


def test_postgres_previous_question_respects_owner_tenant_conversation_and_order(monkeypatch):
    """实际 PostgreSQL 排除跨范围、尚未完成及晚于本轮的提问。"""
    before = (datetime(2026, 1, 2, tzinfo=timezone.utc), "current")
    with database_connection() as conn:
        conn.execute("CREATE TEMP TABLE jobs(id text,tenant_id text,owner_id text,conversation_id text,created_at timestamptz,"
                     "kind text,state text,question text,input_level int,payload jsonb,result jsonb,sources jsonb) ON COMMIT DROP")
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
            created = datetime.fromisoformat(time).replace(tzinfo=timezone.utc)
            conn.execute("INSERT INTO jobs VALUES(%s,%s,%s,%s,%s,'ask',%s,'合成问题',2,'{\"allow_external\":false}',"
                         "jsonb_build_object('citations',jsonb_build_array(jsonb_build_object('chunk_id',%s::text,'cited',true),"
                         "jsonb_build_object('chunk_id','candidate','cited',false),jsonb_build_object('chunk_id','unmarked'))),'[]')",
                         (identifier, tenant, owner, conversation, created, status, identifier))
        row = conversation_question_reference(conn, "t", "u", "c", before)
        assert row["id"] == "before" and row["allow_external"] is False
        assert conversation_question_reference(conn, "t", "u", "c", before, "older")["id"] == "older"
        for reference in ("later", "future", "wrong_tenant", "wrong_owner", "wrong_conversation", "unfinished"):
            assert conversation_question_reference(conn, "t", "u", "c", before, reference) is None

        current_principal = SimpleNamespace(id="u", tenant_id="t", boss=False)
        def fetch_chunk(_conn, _sql, params):
            """替身切片读取函数，返回合成切片内容。

            Args:
                _conn: 被忽略的数据库连接。
                _sql: 被忽略的 SQL 语句。
                params: SQL 绑定参数。
            """
            chunk_id = params[1]
            return {**_document(chunk_id), "id": chunk_id, "text": "合成资料", "node_id": "team"}
        monkeypatch.setattr(memory_service, "fetch_one", fetch_chunk)
        recalled, _ = memory_service.get_followup_context(conn, current_principal, "它的规格呢", 2, "c", before=before)
        assert [doc["chunk_id"] for doc in recalled] == ["before"]
