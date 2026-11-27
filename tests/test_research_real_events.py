"""智能调研无假文案与自治多智能体真实可审计事件流验收测试。"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessageChunk

from taozhi.ai.client import LLMClient
from taozhi.application.research.graph import execute_autonomous_agents
from taozhi.application.research.schemas import (
    _OrchestratorDecisionSchema,
    _ProposedSubtaskSchema,
    _SubagentStepSchema,
)
from taozhi.observability.tracing import job_trace


def test_zero_fake_copy_in_research_workflow():
    """严禁虚假伪造与硬编码：扫描 application/research/ 源码，断言 0 处假思考与假工具文案。"""
    research_dir = Path(__file__).resolve().parents[1] / "src/taozhi/application/research"
    allowed_prefixes = (
        "[agent_start]",
        "[agent_handoff]",
        "[tool_execution_start]",
        "[tool_execution_end]",
        "[agent_end]",
        "[steering]",
        "[system]",
    )
    for src_file in sorted(research_dir.glob("*.py")):
        content = src_file.read_text(encoding="utf-8")

        assert "思考\n总指挥" not in content
        assert "运行工具\n规划电商调研大纲" not in content
        assert "运行工具\n并发执行全网搜索" not in content
        assert "思考\n两路智能体采集完成" not in content
        assert "思考\n全景调研报告编制完成" not in content

        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "emit_progress" and len(node.args) >= 2:
                    stage_node = node.args[1]
                elif node.func.id == "bump_progress" and len(node.args) >= 4:
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
        """记录研究阶段进度事件。

        Args:
            stage: 阶段标识。
            progress: 进度百分比。
        """
        captured_stages.append(stage)

    mock_llm = MagicMock()
    orch_calls = {"n": 0}

    def fake_chat_json(*, prompt: str, schema=None, **kwargs):
        """替身结构化对话模型，覆盖首轮规划与后续子任务决策。

        Args:
            kwargs: 关键字参数。
            prompt: 提示词文本。
            schema: 期望的结构化输出模式。
        """
        if schema is _OrchestratorDecisionSchema:
            orch_calls["n"] += 1
            if orch_calls["n"] == 1:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="派发全网舆情子智能体收集测评",
                    outline=["市场概况", "消费者痛点", "选品建议"],
                    subtasks=[
                        _ProposedSubtaskSchema(
                            role="research.market_intelligence",
                            goal="采集测试保温杯全网评价",
                            suggested_tools=["search_web"],
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
    report_model = MagicMock()
    deltas = []
    def report_stream(*args, **kwargs):
        """替身流式输出：校验流式用量参数并输出含思考片段与最终报告的分块。

        Args:
            args: 位置参数。
            kwargs: 关键字参数。
        """
        assert kwargs == {"stream_usage": True}
        yield AIMessageChunk(content="<thi")
        yield AIMessageChunk(content="nk>内部草稿</th")
        yield AIMessageChunk(content="ink># 测试保温杯市场调研报告")
        assert deltas == ["# 测试保温杯市场调研报告"]
        yield AIMessageChunk(content=[{"type": "reasoning", "reasoning": "内部草稿"}, {"type": "text", "text": "\n\n## 1. 市场概览\n需求旺盛。"}])
        yield AIMessageChunk(content="", usage_metadata={"input_tokens": 15, "output_tokens": 4, "total_tokens": 19})
    report_model.bind.return_value.stream.side_effect = report_stream
    spans = []

    with (
        patch("taozhi.observability.tracing.submit_span", side_effect=spans.append),
        job_trace("tenant_test", "research_1", 1, "task.research"),
        patch("taozhi.application.research.planner.LLMClient", return_value=mock_llm),
        patch("taozhi.application.research.subagents.LLMClient", return_value=mock_llm),
        patch.object(LLMClient, "_client_for_endpoint", return_value=report_model),
    ):
        with patch("taozhi.core.tools.search_web.search_web", return_value=[{"title": "保温杯测评", "snippet": "主流69元" + "x" * 2000}]):
            result = execute_autonomous_agents(
                keyword="测试保温杯",
                job_id="research_1",
                emit_event_fn=mock_emit,
                result_delta_fn=deltas.append,
                result_reset_fn=deltas.clear,
                conversation_id="test_conv_events",
            )

    completed = {s["span_id"]: s for s in spans if s["status"] == "ok"}
    coordinators = sorted(
        (s for s in completed.values() if s["name"] == "research.orchestrator"),
        key=lambda s: s["started_at"],
    )
    assert [s["attributes"]["research.next_action"] for s in coordinators] == ["dispatch_subagents", "synthesizer"]
    assert coordinators[0]["attributes"]["research.dispatched_count"] == 1
    assert "research.dispatched_count" not in coordinators[1]["attributes"]
    tool = next(s for s in completed.values() if s["name"] == "mcp.call_tool")
    assert tool["kind"] == "TOOL" and tool["attributes"]["tool.result_count"] == 1
    branch = completed[tool["parent_span_id"]]
    assert branch["name"] == "research.subagent_worker"
    assert branch["attributes"]["agent.name"] == "research.market_intelligence"
    result_agents = {agent["role"]: agent for agent in result["subagents"]}
    assert result_agents["research.market_intelligence"]["id"] == branch["attributes"]["agent_id"]
    assert tool["attributes"]["tool.name"] == "search_web"
    assert tool["attributes"]["tool.dispatched"] is True
    assert result_agents["research.market_intelligence"]["tools"][0]["dispatched"] is True
    assert not result_agents["research.synthesizer"]["tools"]
    model, = [s for s in completed.values() if s["kind"] == "LLM"]
    report_agent = completed[model["parent_span_id"]]
    assert model["attributes"]["llm.token_count.total"] == 19
    assert report_agent["attributes"]["agent.name"] == "research.synthesizer"
    assert report_agent["attributes"]["agent_id"] == result_agents["research.synthesizer"]["id"]
    by_name = {}
    for recorded in completed.values():
        by_name.setdefault(recorded["name"], []).append(recorded)
    preparation = sorted(by_name["context.prepare_subagent"], key=lambda s: s["started_at"])
    assert [s["attributes"]["context.observation_count"] for s in preparation] == [0, 1]
    assert all(s["parent_span_id"] == branch["span_id"] for s in preparation)
    trim, = by_name["context.trim_observation"]
    assert trim["parent_span_id"] == branch["span_id"]
    assert trim["attributes"]["context.input_chars"] > trim["attributes"]["context.output_chars"] == 1500
    permission, = by_name["research.authorize_tool"]
    assert permission["parent_span_id"] == branch["span_id"] and permission["ended_at"] <= tool["started_at"]
    assert {s["attributes"]["context.evidence_count"] for s in by_name["context.prepare_coordinator"]} == {0, 1}
    report_context, = by_name["context.prepare_report"]
    assert report_context["parent_span_id"] == report_agent["span_id"] and report_context["ended_at"] <= model["started_at"]
    assert report_context["attributes"]["context.evidence_count"] == 1
    assert "需求旺盛" not in str(spans) and "主流69元" not in str(spans)
    assert completed[branch["parent_span_id"]]["name"] == "worker.research"
    root = next(s for s in completed.values() if s["parent_span_id"] is None)
    assert root["name"] == "task.research"
    assert {s["trace_id"] for s in spans} == {result["trace_id"]}
    assert len(captured_stages) >= 4
    assert "".join(deltas) == result["report"] == "# 测试保温杯市场调研报告\n\n## 1. 市场概览\n需求旺盛。"
    agent_starts = [s for s in captured_stages if s.startswith("[agent_start]")]
    assert len(agent_starts) >= 1
    assert "调研协调员" in agent_starts[0]
    assert any(s.startswith("[agent_handoff]") for s in captured_stages)
    assert any(s.startswith("[tool_execution_start]") for s in captured_stages)
    assert any(s.startswith("[tool_execution_end]") for s in captured_stages)
    assert any(s.startswith("[agent_end]") for s in captured_stages)


def test_zero_fake_thinking_in_frontend():
    """验证前端 ResearchChatView.vue 彻底清除提交时预置的假日志占位符。"""
    vue_file = Path(__file__).resolve().parents[1] / "web/src/features/tasks/ResearchChatView.vue"
    content = vue_file.read_text(encoding="utf-8")

    assert "thinkingLogs" not in content and "thinking_logs" not in content
    assert "events: []" in content and 'turns.value = res.turns' in content
