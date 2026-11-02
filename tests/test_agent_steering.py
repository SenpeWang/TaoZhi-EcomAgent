"""运行期动态 Steering（中途追加干预指令与实时纠偏）单元测试。"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from taozhi.core.agent.bus import MessageBus
from taozhi.core.agent.protocol import Message, MessageType, SubagentState, WorkspaceMode
from taozhi.core.agent.runner import SubagentRunner


class MockLLMForSteering:
    """捕获 Prompt 的测试用 LLM 客户端。"""

    def __init__(self):
        self.captured_prompts: List[str] = []

    def chat(self, prompt, **kwargs):
        self.captured_prompts.append(str(prompt))
        return "【研报输出】：已根据中途追加的用户干预指令，重点分析了 1688 源头工厂。"


def test_mid_flight_steering_during_execution(tmp_path):
    """测试智能体在工具执行中途接收人类干预指令并动态纠偏。"""
    bus = MessageBus()
    mock_llm = MockLLMForSteering()
    runner = SubagentRunner(bus=bus, llm_client=mock_llm)

    captured_sub_id = []

    def search_supplier_tool(query: str, limit: int = 15):
        # 模拟在工具运行过程中，外部系统/用户中途追加了一条干预消息
        if captured_sub_id:
            sub_id = captured_sub_id[0]
            runner.manager.send_input(sub_id, "特别关注：只对比 1688 源头工厂报价，忽略第三方代销商")
        return [{"title": "工厂直发爆款保温杯", "price": 18.5, "shop": "金华市某源头五金厂"}]

    # 预先拦截 register_subagent 获取生成的 sub_id
    orig_register = runner.manager.register_subagent
    def hook_register(*args, **kwargs):
        agent = orig_register(*args, **kwargs)
        captured_sub_id.append(agent.conversation_id)
        return agent
    runner.manager.register_subagent = hook_register

    result = runner.invoke_subagent(
        type_name="test_steering_agent",
        role="调研分析师",
        prompt="调研全网智能保温杯爆款",
        custom_tools=[search_supplier_tool],
    )

    assert result.state == SubagentState.COMPLETED
    assert len(mock_llm.captured_prompts) == 1
    final_prompt = mock_llm.captured_prompts[0]

    # 1. 验证运行期中途追加的干预指令被精准注入 Prompt 上下文
    assert "【⚠️ 运行期用户中途追加的修正指令（必须优先遵循）】" in final_prompt
    assert "只对比 1688 源头工厂报价" in final_prompt

    # 2. 验证双轨日志记录了该 STEERING_INTERVENTION 原子步骤
    sub_id = captured_sub_id[0]
    managed_agent = runner.manager._agents[sub_id]
    log_file = managed_agent.context.logs_dir / "transcript.jsonl"
    assert log_file.exists()

    with open(log_file, "r", encoding="utf-8") as f:
        steps = [json.loads(line) for line in f if line.strip()]

    steering_steps = [s for s in steps if s.get("type") == "STEERING_INTERVENTION"]
    assert len(steering_steps) >= 1
    assert "1688 源头工厂报价" in steering_steps[0]["content"]
    assert steering_steps[0]["source"] == "USER_EXPLICIT"