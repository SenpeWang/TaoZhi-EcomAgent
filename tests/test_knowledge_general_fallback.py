"""验证知识问答在无内库依据时的通用能力补充与企业专有事项硬拦截。"""
from __future__ import annotations

from types import SimpleNamespace

from taozhi.application.rag import execution, publication
from taozhi.domain.knowledge_answer import allows_general_knowledge_fallback


def test_allows_general_knowledge_fallback_blocks_enterprise_topics() -> None:
    """验证通用知识兜底仅放行非企业话题，企业相关问题一律拒绝。"""
    assert allows_general_knowledge_fallback("本店退货运费由谁承担？") is False
    assert allows_general_knowledge_fallback("MC-500 型号报价是多少？") is False
    assert allows_general_knowledge_fallback("公司内部请假审批流程是什么？") is False
    assert allows_general_knowledge_fallback("今天是什么日期？") is True
    assert allows_general_knowledge_fallback("氮化镓充电器有什么技术优势？") is True


def _base_state(question: str, *, allow_general_knowledge: bool, allow_external: bool = True) -> dict:
    """构造发布阶段所需的最小任务状态字典。

    Args:
        question: 问题文本。
        allow_general_knowledge: 是否允许通用知识兜底。
        allow_external: 是否允许外部模型处理。
    """
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
    """替换发布阶段的配置、模型与追踪依赖并返回模型提示词列表。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        allow_general_knowledge: 是否允许通用知识兜底。
        allow_external: 是否允许外部模型处理。
        llm_reply: 替身模型的回复文本。
    """
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
        """记录提示词并返回预置回复的模型桩。"""

        def chat(self, prompt: str, **kwargs) -> str:
            """记录提示词并返回预置回复。

            Args:
                prompt: 提示词文本。
                kwargs: 关键字参数。
            """
            calls.append(prompt)
            return llm_reply

        def close(self) -> None:
            """模型桩的关闭方法，无需释放资源。"""
            pass

    monkeypatch.setattr(publication, "LLMClient", FakeLLM)
    return calls


def test_compose_answer_uses_general_knowledge_only_when_allowed_and_non_enterprise(monkeypatch) -> None:
    """验证仅在允许且非企业问题时用通用知识作答，并标注与企业资料无关。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
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
    """验证企业问题即使开启通用知识也硬阻断且不调用模型。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    calls = _patch_runtime(monkeypatch, allow_general_knowledge=True, allow_external=True)
    out = publication.compose_answer(_base_state("本店退货运费怎么算？", allow_general_knowledge=True))

    assert calls == []
    assert out["answer_basis"] == "insufficient_evidence"
    assert out["hard_block"] is True


def test_compose_answer_refuses_when_general_knowledge_disabled(monkeypatch) -> None:
    """验证关闭通用知识后非企业问题也退回资料不足。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    calls = _patch_runtime(monkeypatch, allow_general_knowledge=False, allow_external=True)
    out = publication.compose_answer(_base_state("今天是什么日期？", allow_general_knowledge=False))

    assert calls == []
    assert out["answer_basis"] == "insufficient_evidence"
