"""智能调研无假文案与自治多智能体真实可审计事件流验收测试。"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from taozhi.orchestration.agent_system import execute_autonomous_agents


def test_zero_fake_copy_in_research_workflow():
    """严禁虚假伪造与硬编码：扫描 agent_system.py 源码，断言 0 处假思考与假工具文案。"""
    src_file = Path(__file__).resolve().parents[1] / "src/taozhi/orchestration/agent_system.py"
    content = src_file.read_text(encoding="utf-8")

    # 断言源码中不存在历史遗留的假文本模板
    assert "思考\n总指挥" not in content
    assert "运行工具\n规划电商调研大纲" not in content
    assert "运行工具\n并发执行全网搜索" not in content
    assert "思考\n两路智能体采集完成" not in content
    assert "思考\n全景调研报告编制完成" not in content

    # 断言所有 emit 均采用标准的真实审计事件标签
    for match in re.finditer(r'emit\((?:f)?"([^"]+)"', content):
        emitted_msg = match.group(1)
        assert any(
            emitted_msg.startswith(prefix)
            for prefix in [
                "[agent_start]",
                "[agent_handoff]",
                "[tool_execution_start]",
                "[tool_execution_end]",
                "[agent_end]",
                "[steering]",
                "[system]",
            ]
        ), f"发现未规范的事件输出: {emitted_msg}"


def test_pipeline_a_events_emitted_during_research():
    """测试自治多智能体协同运行全程产生的 stage 100% 来源于真实可审计信标。"""
    captured_stages = []

    def mock_emit(stage: str, progress: int):
        captured_stages.append(stage)

    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        '["市场概况", "源头供应链", "出海对标"]',
        "全网搜索发现：主流款式为超薄磁吸，热销价 39.9 元。",
        "1688 批发发现：东莞工厂一手出厂底价 6.8 元，起订量 100 个。",
        "真实性验真：零售溢价达 5 倍以上，未发现夸大宣传。",
        "# 磁吸保温杯全景调研报告\n\n## 1. 市场概览\n需求旺盛。",
    ]

    # 运行多智能体协同
    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        execute_autonomous_agents(
            keyword="测试保温杯",
            emit_event_fn=mock_emit,
            conversation_id="test_conv_events",
        )

    # 验证按顺序广播了真实的生命周期与工具事件
    assert len(captured_stages) >= 4

    agent_starts = [s for s in captured_stages if s.startswith("[agent_start]")]
    assert len(agent_starts) >= 1
    assert "main_agent" in agent_starts[0]

    agent_handoffs = [s for s in captured_stages if s.startswith("[agent_handoff]")]
    assert len(agent_handoffs) >= 1

    tool_starts = [s for s in captured_stages if s.startswith("[tool_execution_start]")]
    assert len(tool_starts) >= 1

    tool_ends = [s for s in captured_stages if s.startswith("[tool_execution_end]")]
    assert len(tool_ends) >= 1

    agent_ends = [s for s in captured_stages if s.startswith("[agent_end]")]
    assert len(agent_ends) >= 1


def test_zero_fake_thinking_in_frontend():
    """验证前端 ResearchChatView.vue 彻底清除提交时预置的假日志占位符。"""
    vue_file = Path(__file__).resolve().parents[1] / "web/src/views/ResearchChatView.vue"
    content = vue_file.read_text(encoding="utf-8")

    # 断言前端提交临时轮次时没有假日志
    assert "thinkingLogs: ['思考\\n总指挥" not in content
    assert "thinkingLogs: []" in content
