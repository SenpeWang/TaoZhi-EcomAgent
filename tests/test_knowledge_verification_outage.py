"""专业模型无法核验时，只将与问题匹配的授权原文交给人工核对。"""

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import Mock

import pytest

from taozhi.application.rag import publication, specialists
from taozhi.application.rag.execution import _build_question_result


@pytest.fixture
def knowledge_state(monkeypatch):
    def document(identifier, quote):
        return {"document_id": identifier, "chunk_id": identifier, "version": 1, "page": 1,
                "level": 1, "title": "合成售后政策", "quote": quote}

    job = {"input_level": 1, "payload": {"require_review": True, "allow_public_reference": True}}
    for module in (publication, specialists):
        monkeypatch.setattr(module, "validate_task_execution", lambda *_: (None, job))
    return {
        "job_id": "test-job", "run_version": 1, "question": "A1 线下门店保修多久？",
        "selected_agents": ["after_sales"], "usage": [], "model_calls": 0,
        "hard_block": False, "external_blocked": False, "business": [], "sources": [],
        "evidence": [
            document("wrong-model", "B2 线下门店保修一年。"),
            document("wrong-channel", "A1 线上订单保修六个月。"),
            document("applicable", "A1 线下门店保修三个月，但仅限持购买凭证的客户。"),
        ],
    }


@pytest.mark.parametrize("mode,issue,action,calls", [
    ("unconfigured", "model_unconfigured", "联系管理员配置", 0),
    ("budget_before", "budget_exhausted", "聚焦一个问题", 0),
    ("budget_locked", "budget_exhausted", "聚焦一个问题", 0),
    ("failed", "model_unavailable", "模型服务恢复", 1),
])
def test_unavailable_specialists_publish_only_relevant_excerpts(monkeypatch, knowledge_state, mode, issue, action, calls):
    settings = specialists.taozhi_config.get_settings().model_copy(update={"api_key": "" if mode == "unconfigured" else "fixture"})
    config = replace(specialists.taozhi_config.load_config(), max_model_calls=1)
    monkeypatch.setattr(specialists.taozhi_config, "get_settings", lambda: settings)
    monkeypatch.setattr(specialists.taozhi_config, "load_config", lambda: config)
    monkeypatch.setattr(specialists, "database_connection", nullcontext)
    counts = {"budget_before": [1], "budget_locked": [0, 1]}.get(mode, [0, 0])
    monkeypatch.setattr(specialists, "count_model_calls", Mock(side_effect=counts))
    monkeypatch.setattr(specialists, "authorized_running_task", Mock())
    monkeypatch.setattr(specialists, "lock_running_job", Mock(return_value={"tenant_id": "tenant"}))
    reserve = Mock(return_value="call")
    monkeypatch.setattr(specialists, "insert_model_call", reserve)
    monkeypatch.setattr(specialists, "update_model_call", Mock())
    client = Mock()
    client.chat_json.side_effect = TimeoutError("合成模型请求超时")
    monkeypatch.setattr(specialists, "LLMClient", Mock(return_value=client))
    state = knowledge_state
    state.update(specialists.extract_claims(state))
    state.update(specialists.verify_claims(state))
    state.update(publication.compose_answer(state))
    result = _build_question_result(state)
    assert state["verification_issue"] == issue and action in result["guidance"]
    assert result["answer_basis"] == "source_excerpt" and "自动核验未完成" in result["answer"]
    assert state["review"] and not state["findings"] and result["missing"] and not result["public_sources"]
    assert result["model_calls"] == reserve.call_count == client.chat_json.call_count == calls
    assert [citation["cited"] for citation in result["citations"]] == [False, False, True]
    assert state["evidence"][2]["quote"] + " [3]" in result["answer"]
    assert "一年" not in result["answer"] and "六个月" not in result["answer"]


@pytest.mark.parametrize("question,blocked,issue,basis", [
    ("C3 线下门店保修多久？", False, True, "insufficient_evidence"),
    ("什么是 USB PD？", False, True, "public_reference"),
    ("什么是 USB PD？", True, False, "public_reference"),
    ("A1 线下门店保修多久？", False, False, "insufficient_evidence"),
])
def test_unrelated_or_reviewed_candidates_remain_uncited(knowledge_state, question, blocked, issue, basis):
    state = knowledge_state
    state.update(question=question, external_blocked=blocked, findings=[], missing=[])
    if issue:
        state["verification_issue"] = "model_unavailable"
    state.update(publication.compose_answer(state))
    result = _build_question_result(state)
    assert result["answer_basis"] == basis and not any(item["cited"] for item in result["citations"])
    assert all(item["quote"] not in result["answer"] for item in state["evidence"])


def test_distinct_policy_rows_cannot_combine_into_a_manual_reference(knowledge_state):
    state = knowledge_state
    state.update(verification_issue="model_unavailable", findings=[], missing=[])
    state["evidence"][2]["quote"] = "A1 线下门店提供清洁服务。\nB2 线下门店保修三个月。"
    state.update(publication.compose_answer(state))
    assert state["answer_basis"] == "insufficient_evidence" and not state["excerpt_indices"]


def test_partial_specialist_failure_preserves_verified_findings(knowledge_state):
    state = knowledge_state
    quote = state["evidence"][2]["quote"]
    state.update(verification_issue="model_unavailable", missing=["另一个专业核验模型暂时不可用。"],
                 findings=[{"role": "after_sales", "evidence_index": 2, "claim": quote, "quote": quote}])
    state.update(publication.compose_answer(state))
    result = _build_question_result(state)
    assert result["answer_basis"] == "internal_evidence" and result["answer"] == quote + " [3]"
    assert state["excerpt_indices"] == [] and [doc["cited"] for doc in result["citations"]] == [False, False, True]
