"""速答 Worker：Supervisor 简单问题一次问答、工具边界与研究路径回归。"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from taozhi.application.research.graph import execute_autonomous_agents
from taozhi.application.research.schemas import _OrchestratorDecisionSchema, _QuickAnswerSchema


@contextmanager
def _patch_llm(mock_llm):
    """替换研究编排与子智能体模型客户端为替身。

    Args:
        mock_llm: 替身模型客户端。
    """
    with (
        patch("taozhi.application.research.planner.LLMClient", return_value=mock_llm),
        patch("taozhi.application.research.subagents.LLMClient", return_value=mock_llm),
        patch("taozhi.application.research.reporter.LLMClient", return_value=mock_llm),
        patch("taozhi.application.research.quick_answer.LLMClient", return_value=mock_llm),
    ):
        yield


def _quick_llm(quick_decision: _QuickAnswerSchema, answer_after_tool: str = "") -> MagicMock:
    """构造快速回答模式的模型替身，记录提示词并按阶段返回决策。

    Args:
        quick_decision: 快速通道的编排决策。
        answer_after_tool: 工具执行后的回答文本。
    """
    mock = MagicMock()
    prompts = []
    mock.prompts = prompts

    def fake_chat_json(*, prompt: str, schema=None, **_kwargs):
        """替身结构化对话模型，返回快速通道的编排决策。

        Args:
            _kwargs: 被忽略的关键字参数。
            prompt: 提示词文本。
            schema: 期望的结构化输出模式。
        """
        prompts.append(prompt)
        if schema is _OrchestratorDecisionSchema:
            return _OrchestratorDecisionSchema(
                action="quick_answer",
                thought="简单问题，一次回答即可",
                outline=[],
            )
        if schema is _QuickAnswerSchema:
            return quick_decision
        raise AssertionError(f"unexpected schema: {schema}")

    mock.chat_json.side_effect = fake_chat_json
    mock.chat.return_value = answer_after_tool
    return mock


def test_simple_question_uses_quick_answer_without_research_report():
    """Supervisor 选择速答后，不经过研报合成，直接返回简洁答案。"""
    mock = _quick_llm(_QuickAnswerSchema(action="answer", thought="无需工具", answer="USB-C 是可正反插的接口标准。"))
    with _patch_llm(mock):
        result = execute_autonomous_agents(keyword="USB-C", user_query="什么是 USB-C？")

    assert result["report"] == "USB-C 是可正反插的接口标准。"
    assert result["outline"] == []
    assert result["system_status"] == "COMPLETED"
    assert result["stop_reason"] == "completed"
    mock.chat.assert_not_called()
    assert len(result["subagents"]) == 1
    assert result["subagents"][0]["role"] == "research.quick_answer"
    assert result["evidence_pool"][0]["role"] == "research.quick_answer"
    assert any("速答专员" in prompt for prompt in mock.prompts)
    assert any("最多调用一次" in prompt for prompt in mock.prompts)


def test_quick_answer_can_use_one_web_tool():
    """速答 Worker 允许一次网页检索，并把结果作为证据回写。"""
    decision = _QuickAnswerSchema(
        action="call_tool",
        thought="检索一次公开网页",
        tool_name="search_web",
        query_or_url="手机壳 主流价格",
    )
    mock = _quick_llm(decision, answer_after_tool="主流价格约 39.9 元。")
    with (
        _patch_llm(mock),
        patch(
            "taozhi.core.tools.search_web.search_web",
            return_value=[{"title": "价格行情", "snippet": "39.9 元", "url": "https://example.com/a", "source": "test"}],
        ),
    ):
        result = execute_autonomous_agents(keyword="手机壳", user_query="手机壳目前主流价格是多少？")

    tools = result["subagents"][0]["tools"]
    assert [tool["name"] for tool in tools] == ["search_web"]
    evidence = result["evidence_pool"][0]
    assert evidence["channel"] == "web"
    assert evidence["platform_items"]["web"][0]["url"] == "https://example.com/a"
    assert result["report"] == "主流价格约 39.9 元。"


def test_quick_answer_rejects_sourcing_tool_and_does_not_fabricate():
    """速答 Worker 不得调用 1688/Amazon；越权时如实回答资料不足。"""
    decision = _QuickAnswerSchema(
        action="call_tool",
        thought="尝试直接查 1688",
        tool_name="search_1688_wholesale",
        query_or_url="保温杯",
    )
    mock = _quick_llm(decision)
    with _patch_llm(mock):
        result = execute_autonomous_agents(keyword="保温杯", user_query="保温杯 全网口碑怎么样？")

    assert result["subagents"][0]["tools"] == []
    assert result["evidence_pool"][0]["status"] == "error"
    assert "没有足够" in result["report"]
    mock.chat.assert_not_called()


def test_quick_answer_empty_tool_result_is_reported_as_gap():
    """工具空结果时速答回答必须说明缺失，不生成虚假数字。"""
    decision = _QuickAnswerSchema(
        action="call_tool",
        thought="检索一次",
        tool_name="search_web",
        query_or_url="冷门配件 价格",
    )
    mock = _quick_llm(decision, answer_after_tool="")
    with (
        _patch_llm(mock),
        patch("taozhi.core.tools.search_web.search_web", return_value=[]),
    ):
        result = execute_autonomous_agents(keyword="冷门配件", user_query="冷门配件多少钱？")

    assert result["evidence_pool"][0]["status"] in ("empty", "error")
    assert "没有足够" in result["report"]
    assert result["evidence_pool"][0]["gaps_or_errors"]
