"""知识库专家选择、排队执行与模型外发授权回归。"""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from taozhi.application.common import task_guard
from taozhi.application.rag import intent, specialists
from taozhi.domain.errors import Denied
from taozhi.orchestration.decision import DecisionPath


@pytest.mark.parametrize(("question", "expected"), [
    ("这款保护膜兼容吗？", ["compatibility"]),
    ("请提供新人培训的操作步骤", ["coach"]),
    ("这批订单的毛利如何结算？", ["finance"]),
    ("上架文案里的卖点是什么？", ["operations"]),
    ("售后运费由谁承担？", ["after_sales"]),
    ("日常供货流程有哪些？", ["supply"]),
    ("请列出型号、适配机型和保修期", ["product", "compatibility", "after_sales"]),
    ("这是什么？", ["product"]),
])
def test_question_routes_to_explicit_roles_without_embeddings(monkeypatch, question, expected):
    monkeypatch.setattr(intent, "validate_task_execution", lambda *_: (None, {"question": question, "input_level": 1}))
    monkeypatch.setattr("taozhi.orchestration.decision.embedding_available", lambda: False)
    assert intent.plan_node({})["selected_agents"] == expected


def test_experts_respect_specification_priority_semantic_choice_and_limit(monkeypatch):
    question = "给新人说明尺寸、保修政策、发货时间"
    monkeypatch.setattr(intent, "validate_task_execution", lambda *_: (None, {"question": question, "input_level": 1}))
    engine = Mock()
    engine.choice.return_value = SimpleNamespace(selected=["coach"], path=DecisionPath.FAST_SEMANTIC)
    monkeypatch.setattr(intent, "get_decision_engine", lambda: engine)
    assert intent.plan_node({})["selected_agents"] == ["product", "coach", "after_sales"]
    assert engine.choice.call_args.kwargs["allow_llm"] is False


def test_waiting_expert_rechecks_external_permission_before_reserving_model_call(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "job_id": "task", "run_version": 1, "question": "规格和售后", "selected_agents": ["product", "after_sales"],
        "evidence": [{"title": "产品手册", "quote": "规格和售后"}], "sources": [{"document_id": "doc"}], "business": [],
    }
    job = {
        "id": "task", "tenant_id": "tenant", "owner_id": "staff", "owner_version": 1, "run_version": 1,
        "state": "running", "cancel_requested": False, "lease_until": now + timedelta(minutes=2),
        "started_at": now, "input_level": 1, "payload": {},
    }
    settings = specialists.taozhi_config.get_settings().model_copy(update={"api_key": "test"})
    monkeypatch.setenv("ECOM_MODE", "test")
    monkeypatch.setenv("ECOM_DATABASE_URL", "postgresql://127.0.0.1/ecom_test")
    monkeypatch.setenv("ECOM_SPECIALIST_CONCURRENCY", "1")
    monkeypatch.setattr(specialists.taozhi_config, "get_settings", lambda: settings)
    monkeypatch.setattr(specialists, "validate_task_execution", lambda *_: (None, job))
    monkeypatch.setattr(specialists, "database_connection", nullcontext)
    monkeypatch.setattr(task_guard, "get_job_by_id", lambda *_: job)
    monkeypatch.setattr(task_guard, "load_principal", lambda *_: SimpleNamespace(version=1, boss=False))
    checks = Mock(side_effect=[True, False])
    monkeypatch.setattr(task_guard, "sources_valid", checks)
    reserve = Mock(return_value="call-1")
    update = Mock()
    monkeypatch.setattr(specialists, "lock_running_job", lambda *_: job)
    monkeypatch.setattr(specialists, "count_model_calls", lambda *_: 0)
    monkeypatch.setattr(specialists, "insert_model_call", reserve)
    monkeypatch.setattr(specialists, "update_model_call", update)
    pool = Mock(wraps=specialists.ThreadPoolExecutor)
    monkeypatch.setattr(specialists, "ThreadPoolExecutor", pool)
    client = Mock()
    client.chat_json.return_value = {"findings": [], "missing": []}
    monkeypatch.setattr(specialists, "LLMClient", lambda *_args, **_kwargs: client)

    with pytest.raises(Denied) as denied:
        specialists.domain_node(state)

    assert denied.value.code == "SOURCE_CHANGED"
    pool.assert_called_once_with(max_workers=1)
    assert [call.args[2:] for call in checks.call_args_list] == [(state["sources"], True)] * 2
    reserve.assert_called_once_with(None, "tenant", "task", "product")
    client.chat_json.assert_called_once()
    assert update.call_args.args[-1] == "ok"
