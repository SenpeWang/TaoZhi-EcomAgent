"""验证知识问答在无内库依据时的通用能力补充与企业专有事项硬拦截。"""
from __future__ import annotations

from types import SimpleNamespace

from taozhi.application.rag import execution, publication
from taozhi.domain.knowledge_answer import allows_general_knowledge_fallback


def test_allows_general_knowledge_fallback_blocks_enterprise_topics() -> None:
    assert allows_general_knowledge_fallback("本店退货运费由谁承担？") is False
    assert allows_general_knowledge_fallback("MC-500 型号报价是多少？") is False
    assert allows_general_knowledge_fallback("公司内部请假审批流程是什么？") is False
    assert allows_general_knowledge_fallback("今天是什么日期？") is True
    assert allows_general_knowledge_fallback("氮化镓充电器有什么技术优势？") is True


def _base_state(question: str, *, allow_general_knowledge: bool, allow_external: bool = True) -> dict:
    return {
        "job_id": "job_1",
        "run_version": 1,
        "question": question,
        "evidence": [],
        "business": [],
        "missing": ["未检索到匹配的授权资料"],
        "findings": [],
        "sources": [],
        "live_fields": [],
        "hard_block": True,
        "external_blocked": not allow_external,
        "verification_issue": None,
        "input_level": 1,
        "prior_question_id": "",
        "has_recalled_context": False,
        "token_usage": None,
        "model_calls": 0,
    }


def _patch_runtime(monkeypatch, *, allow_general_knowledge: bool, allow_external: bool = True, llm_reply: str = "今天是测试日期。"):
    calls: list[str] = []

    monkeypatch.setattr(
        publication,
        "validate_task_execution",
        lambda state, stage, progress: (
            SimpleNamespace(id="u1", tenant_id="t1"),
            {
                "id": "job_1",
                "input_level": 1,
                "payload": {
                    "allow_public_reference": True,
                    "allow_external": allow_external,
                    "allow_general_knowledge": allow_general_knowledge,
                    "require_review": False,
                },
            },
        ),
    )
    monkeypatch.setattr(publication, "get_settings", lambda: SimpleNamespace(has_llm=True))

    class FakeLLM:
        def chat(self, prompt: str, **kwargs) -> str:
            calls.append(prompt)
            return llm_reply

        def close(self) -> None:
            pass

    monkeypatch.setattr(publication, "LLMClient", FakeLLM)
    return calls


def test_compose_answer_uses_general_knowledge_only_when_allowed_and_non_enterprise(monkeypatch) -> None:
    calls = _patch_runtime(monkeypatch, allow_general_knowledge=True, allow_external=True, llm_reply="今天是 2026 年 11 月 23 日。")
    out = publication.compose_answer(_base_state("今天是什么日期？", allow_general_knowledge=True))

    assert len(calls) == 1
    assert out["answer_basis"] == "general_knowledge"
    assert "2026 年 11 月 23 日" in out["answer"]
    assert "与企业内部知识库及官方参考无关" in out["guidance"]
    assert out["hard_block"] is False
    assert out["missing"] == []
    assert out["model_calls"] == 1

    built = execution._build_question_result(
        {
            **_base_state("今天是什么日期？", allow_general_knowledge=True),
            **out,
            "selected_agents": ["product"],
            "usage": [],
            "token_usage": out["token_usage"],
        }
    )
    assert built["specialists"] == []


def test_compose_answer_still_refuses_enterprise_question_even_with_general_knowledge(monkeypatch) -> None:
    calls = _patch_runtime(monkeypatch, allow_general_knowledge=True, allow_external=True)
    out = publication.compose_answer(_base_state("本店退货运费怎么算？", allow_general_knowledge=True))

    assert calls == []
    assert out["answer_basis"] == "insufficient_evidence"
    assert out["hard_block"] is True


def test_compose_answer_refuses_when_general_knowledge_disabled(monkeypatch) -> None:
    calls = _patch_runtime(monkeypatch, allow_general_knowledge=False, allow_external=True)
    out = publication.compose_answer(_base_state("今天是什么日期？", allow_general_knowledge=False))

    assert calls == []
    assert out["answer_basis"] == "insufficient_evidence"
