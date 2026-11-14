"""智能调研无假文案与自治多智能体真实可审计事件流验收测试。"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from taozhi.orchestration.agent_system import (
    _OrchestratorDecisionSchema,
    _ProposedSubtaskSchema,
    _SubagentStepSchema,
    execute_autonomous_agents,
)


def test_zero_fake_copy_in_research_workflow():
    """严禁虚假伪造与硬编码：扫描 agent_system.py 源码，断言 0 处假思考与假工具文案。"""
    src_file = Path(__file__).resolve().parents[1] / "src/taozhi/orchestration/agent_system.py"
    content = src_file.read_text(encoding="utf-8")

    assert "思考\n总指挥" not in content
    assert "运行工具\n规划电商调研大纲" not in content
    assert "运行工具\n并发执行全网搜索" not in content
    assert "思考\n两路智能体采集完成" not in content
    assert "思考\n全景调研报告编制完成" not in content

    allowed_prefixes = (
        "[agent_start]",
        "[agent_handoff]",
        "[tool_execution_start]",
        "[tool_execution_end]",
        "[agent_end]",
        "[steering]",
        "[system]",
    )
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_emit_progress" and len(node.args) >= 2:
                stage_node = node.args[1]
            elif node.func.id == "_bump_progress" and len(node.args) >= 4:
                stage_node = node.args[3]
            else:
                continue
            if isinstance(stage_node, ast.Constant) and isinstance(stage_node.value, str):
                text = stage_node.value
            elif isinstance(stage_node, ast.JoinedStr) and stage_node.values and isinstance(stage_node.values[0], ast.Constant):
                text = str(stage_node.values[0].value)
            else:
                continue
            assert any(text.startswith(prefix) for prefix in allowed_prefixes), f"发现未规范的事件输出: {text}"


def test_pipeline_a_events_emitted_during_research():
    """测试自治多智能体协同运行全程产生的 stage 100% 来源于真实可审计信标。"""
    captured_stages = []

    def mock_emit(stage: str, progress: int):
        captured_stages.append(stage)

    mock_llm = MagicMock()
    orch_calls = {"n": 0}

    def fake_chat_json(*, prompt: str, schema=None, **kwargs):
        if schema is _OrchestratorDecisionSchema:
            orch_calls["n"] += 1
            if orch_calls["n"] == 1:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="派发全网舆情子智能体收集测评",
                    outline=["市场概况", "消费者痛点", "选品建议"],
                    subtasks=[
                        _ProposedSubtaskSchema(
                            role="全网市场情报分析专员",
                            goal="采集测试保温杯全网评价",
                            allowed_tools=["search_web"],
                            seed_queries=["测试保温杯"],
                            channel_hint="web",
                        )
                    ],
                )
            return _OrchestratorDecisionSchema(
                action="finish",
                thought="证据足够生成研报",
                outline=["市场概况", "消费者痛点", "选品建议"],
            )

        if "历史观测记录：\n[]" in prompt:
            return _SubagentStepSchema(
                action="call_tool",
                thought="执行全网检索",
                tool_name="search_web",
                query_or_url="测试保温杯",
            )
        return _SubagentStepSchema(
            action="finish",
            thought="已获实证",
            findings_summary="全网主流保温杯定价 69-129 元。",
            key_metrics=["69-129元"],
        )

    mock_llm.chat_json.side_effect = fake_chat_json
    mock_llm.chat.return_value = "# 测试保温杯市场调研报告\n\n## 1. 市场概览\n需求旺盛。"

    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        with patch("taozhi.orchestration.agent_system.search_web", return_value=[{"title": "保温杯测评", "snippet": "主流69元"}]):
            execute_autonomous_agents(
                keyword="测试保温杯",
                emit_event_fn=mock_emit,
                conversation_id="test_conv_events",
            )

    assert len(captured_stages) >= 4
    agent_starts = [s for s in captured_stages if s.startswith("[agent_start]")]
    assert len(agent_starts) >= 1
    assert "main_agent" in agent_starts[0]
    assert any(s.startswith("[agent_handoff]") for s in captured_stages)
    assert any(s.startswith("[tool_execution_start]") for s in captured_stages)
    assert any(s.startswith("[tool_execution_end]") for s in captured_stages)
    assert any(s.startswith("[agent_end]") for s in captured_stages)


def test_zero_fake_thinking_in_frontend():
    """验证前端 ResearchChatView.vue 彻底清除提交时预置的假日志占位符。"""
    vue_file = Path(__file__).resolve().parents[1] / "web/src/views/ResearchChatView.vue"
    content = vue_file.read_text(encoding="utf-8")

    assert "thinkingLogs: ['思考\\n总指挥" not in content
    assert "thinkingLogs: []" in content
