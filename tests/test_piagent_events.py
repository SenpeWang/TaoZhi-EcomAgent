"""PiAgent 规范双管道事件体系验收测试。

覆盖：
1. 管道 A（只读订阅通知）：agent_start/end, turn_start/end, tool_execution_* 异常隔离；
2. 管道 B（tool_call 权限闸门）：短路阻断、裸 await 异常 Fail-Closed；
3. 管道 B（tool_result 统一出口责任链）：顺序链式改写、PII 敏感脱敏与有界截断。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from taozhi.core.agent.events import (
    AgentEventHub,
    AgentEventType,
    HookEventType,
    ToolCallAction,
    ToolResultAction,
)
from taozhi.core.agent.protocol import SubagentState
from taozhi.core.agent.runner import SubagentRunner


class DummyLLM:
    def chat(self, prompt, **kwargs):
        return "【研报】：分析完成。"


def test_pipeline_a_readonly_notifications():
    """测试管道 A（只读通知）：通知事件完整广播且订阅者异常完全隔离。"""
    hub = AgentEventHub()
    events_captured = []

    def handle_start(**payload):
        events_captured.append(("start", payload.get("subagent_id")))

    def handle_turn(**payload):
        events_captured.append(("turn", payload.get("turn")))

    def faulty_subscriber(**payload):
        raise RuntimeError("管道 A 订阅者内部崩溃，不应影响 Agent 主循环")

    hub.subscribe(AgentEventType.AGENT_START, handle_start)
    hub.subscribe(AgentEventType.TURN_START, handle_turn)
    hub.subscribe(AgentEventType.AGENT_START, faulty_subscriber)

    runner = SubagentRunner(event_hub=hub, llm_client=DummyLLM())
    res = runner.invoke_subagent(
        type_name="test_agent",
        role="分析师",
        prompt="测试管道 A 广播",
        custom_tools=[],
    )

    assert res.state == SubagentState.COMPLETED
    assert any(e[0] == "start" for e in events_captured)
    assert any(e[0] == "turn" for e in events_captured)


def test_pipeline_b_tool_call_blocking_gate():
    """测试管道 B（tool_call 权限闸门）：阻断返回瞬间短路，底层工具绝不执行。"""
    hub = AgentEventHub()
    tool_executed = False

    def dangerous_rm_tool(query: str, limit: int = 15):
        nonlocal tool_executed
        tool_executed = True
        return [{"status": "deleted"}]

    # 注册 tool_call 高危命令审批拦截扩展
    def eda_approval_gate(tool_name: str, arguments: any, context: any) -> ToolCallAction:
        if "rm" in tool_name or "dangerous" in tool_name:
            return ToolCallAction(
                block=True,
                reason="高危操作审批拦截：生产环境禁止直接调用删除或清理类工具！",
            )
        return ToolCallAction(block=False)

    hub.on(HookEventType.TOOL_CALL, eda_approval_gate)
    runner = SubagentRunner(event_hub=hub, llm_client=DummyLLM())

    res = runner.invoke_subagent(
        type_name="test_agent",
        role="高危工具测试专员",
        prompt="测试删除指令",
        custom_tools=[dangerous_rm_tool],
    )

    # 验证底层高危工具被短路阻断，根本没有执行
    assert not tool_executed
    assert res.state == SubagentState.COMPLETED

    # 验证双轨日志中记录了权限拒绝原因
    managed_agent = runner.manager._agents[res.subagent_id]
    log_file = managed_agent.context.logs_dir / "transcript.jsonl"
    with open(log_file, "r", encoding="utf-8") as f:
        steps = [json.loads(line) for line in f if line.strip()]

    blocked_steps = [s for s in steps if "blocked by policy" in s.get("content", "")]
    assert len(blocked_steps) >= 1
    assert "高危操作审批拦截" in blocked_steps[0]["content"]


def test_pipeline_b_tool_call_fail_closed_without_try_catch():
    """测试管道 B（tool_call）：闸门代码崩溃时裸 await 抛出，绝不静默放行（Fail-Closed）。"""
    hub = AgentEventHub()

    def buggy_gate(tool_name: str, arguments: any, context: any) -> ToolCallAction:
        raise PermissionError("权限系统配置损坏")

    hub.on(HookEventType.TOOL_CALL, buggy_gate)
    runner = SubagentRunner(event_hub=hub, llm_client=DummyLLM())

    def dummy_tool(query: str):
        return []

    res = runner.invoke_subagent(
        type_name="test_agent",
        role="故障测试",
        prompt="测试闸门崩溃",
        custom_tools=[dummy_tool],
    )

    # 证明 Fail-Closed：权限闸门故障直接导致任务失败，杜绝了静默放行的安全隐患
    assert res.state == SubagentState.FAILED
    assert "PermissionError" in res.error


def test_pipeline_b_tool_result_mutation_chain():
    """测试管道 B（tool_result 统一出口）：责任链顺序清洗，自动脱敏 PII 且下游统一受益。"""
    hub = AgentEventHub()

    # 模拟工具返回包含敏感手机号、API Key与客户地址的原始数据
    def sensitive_customer_tool(query: str, limit: int = 15):
        return [
            {
                "customer_name": "张经理",
                "phone": "13812345678",
                "api_key": "sk-" + "test-token-12345678901234567890",
                "address": "收货地址：杭州市滨江区网商路699号",
            }
        ]

    runner = SubagentRunner(event_hub=hub, llm_client=DummyLLM())
    res = runner.invoke_subagent(
        type_name="test_agent",
        role="客户数据核查员",
        prompt="查询客户档案",
        custom_tools=[sensitive_customer_tool],
    )

    if res.state != SubagentState.COMPLETED:
        raise RuntimeError(f"invoke_subagent failed with error: {res.error}")

    managed_agent = runner.manager._agents[res.subagent_id]

    # 1. 验证落盘 scratch 的文件已经过 tool_result 自动脱敏
    scratch_file = managed_agent.context.scratch_dir / "raw_sensitive_customer_tool.json"
    assert scratch_file.exists()
    content = scratch_file.read_text(encoding="utf-8")

    assert "138****5678" in content
    assert "13812345678" not in content
    assert "已隐藏的密钥" in content
    assert "sk-" + "test-token" not in content
    assert "收货地址：已隐藏" in content

    # 2. 验证写入双轨日志 transcript.jsonl 的文本同样 100% 脱敏
    log_file = managed_agent.context.logs_dir / "transcript.jsonl"
    with open(log_file, "r", encoding="utf-8") as f:
        steps = [json.loads(line) for line in f if line.strip()]

    tool_results = [s for s in steps if s.get("type") == "TOOL_RESULT"]
    assert len(tool_results) >= 1
    logged_content = tool_results[0]["content"]
    assert "138****5678" in logged_content
    assert "13812345678" not in logged_content