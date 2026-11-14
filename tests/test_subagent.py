"""测试自研多智能体协同底座与电商网络搜索流水线。

覆盖：
1. 协议定义与注册表管理 (protocol & registry)；
2. 反应式消息总线 (MessageBus) 与事件投递；
3. 执行上下文 (SubagentContext) 与 scratch/artifacts 隔离；
4. 全网搜索工具 (search_web)；
5. 子智能体运行引擎 (SubagentRunner) 并发执行；
6. 完整的多智能体调研系统 (run_research)。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from taozhi.orchestration.agent_system import execute_autonomous_agents
from taozhi.core.agent import (
    Message,
    MessageBus,
    MessageType,
    SubagentContext,
    SubagentDefinition,
    SubagentRegistry,
    SubagentResult,
    SubagentRunner,
    SubagentState,
    WorkspaceMode,
    default_registry,
    search_web,
)


def test_subagent_registry():
    """测试智能体类型的注册与获取（统一以 _agent 为后缀）。"""
    reg = SubagentRegistry()
    assert reg.get("main_agent") is not None
    assert reg.get("web_agent") is not None
    assert reg.get("scanner_agent") is not None
    assert reg.get("verifier_agent") is not None
    assert reg.get("reporter_agent") is not None
    # 验证简写别名自适应映射
    assert reg.get("main") is not None
    assert reg.get("web") is not None
    assert reg.get("scanner") is not None
    assert reg.get("verifier") is not None
    assert reg.get("reporter") is not None

    custom = reg.define_subagent(
        name="custom_analyst_agent",
        role="定制品类分析师",
        description="用于分析冷门长尾商品",
        system_prompt="你是定制品类专家。",
    )
    assert custom.name == "custom_analyst_agent"
    assert reg.get("custom_analyst_agent") == custom


def test_message_bus_routing_and_reactive_wait():
    """测试反应式消息总线投递与条件唤醒（No Polling）。"""
    bus = MessageBus()
    received_msgs = []

    def on_agent_msg(msg: Message):
        received_msgs.append(msg)

    bus.subscribe("agent_b", on_agent_msg)

    # 1. 普通投递
    msg1 = Message(
        sender_id="agent_a",
        recipient_id="agent_b",
        msg_type=MessageType.TASK,
        content="请搜索 2026 年最新手机壳",
    )
    bus.post(msg1)

    assert len(received_msgs) == 1
    assert received_msgs[0].content == "请搜索 2026 年最新手机壳"

    # 2. 跨线程反应式等待 (wait_for_message)
    def delayed_send():
        time.sleep(0.1)
        bus.post(
            Message(
                sender_id="agent_b",
                recipient_id="agent_a",
                msg_type=MessageType.RESULT,
                content="任务完成",
            )
        )

    t = threading.Thread(target=delayed_send)
    t.start()

    res = bus.wait_for_message("agent_a", timeout=2.0)
    t.join()

    assert res is not None
    assert res.content == "任务完成"
    assert res.msg_type == MessageType.RESULT


def test_subagent_context_isolation(tmp_path):
    """测试智能体隔离目录、scratch 暂存与 artifacts 交付。"""
    ctx = SubagentContext(
        conversation_id="test_sub_001",
        role="测试专员",
        workspace_mode=WorkspaceMode.BRANCH,
        root_dir=tmp_path,
    )

    assert ctx.scratch_dir.exists()
    assert ctx.artifacts_dir.exists()

    # 写入 scratch 原始数据
    p1 = ctx.write_scratch("raw_data.json", '{"items": [1, 2, 3]}')
    assert p1.exists()
    assert str(p1) in ctx.get_scratch_files()

    # 写入交付工件
    p2 = ctx.write_artifact("report.md", "# 交付报告")
    assert p2.exists()
    assert str(p2) in ctx.get_artifacts()

    # 清理 scratch 验证
    ctx.cleanup()
    assert not ctx.scratch_dir.exists()
    assert ctx.artifacts_dir.exists()  # 工件予以保留


def test_search_web_tool():
    """测试网络搜索工具具备优雅降级与数据解析。"""
    fake_html = """
    <a class="result__url" href="https://example.com/item1"></a>
    <h2><a href="https://example.com/item1">苹果16磁吸手机壳深度测评</a></h2>
    <a class="result__snippet">2026年最新款式，超强吸力与磨砂质感防滑设计。</a>
    """
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = fake_html
        mock_post.return_value = mock_resp

        results = search_web("手机壳", limit=5)
        assert len(results) == 1
        assert "苹果16磁吸手机壳深度测评" in results[0]["title"]
        assert "超强吸力" in results[0]["snippet"]


def test_subagent_runner_invoke():
    """测试 SubagentRunner 派发子智能体及产物构建。"""
    mock_llm = MagicMock()
    mock_llm.chat.return_value = "【市场情报要点】：1. 磁吸支架款式销量大涨；2. 均价在 35-50 元。"

    bus = MessageBus()
    runner = SubagentRunner(bus=bus, llm_client=mock_llm)

    res = runner.invoke_subagent(
        type_name="web_agent",
        role="全网市场情报分析专员",
        prompt="调研磁吸手机壳市场",
        model_tier="fast",
    )

    assert res.state == SubagentState.COMPLETED
    assert res.role == "全网市场情报分析专员"
    assert "磁吸支架" in res.summary
    assert len(res.artifacts) > 0


def test_full_multi_agent_research_flow():
    """测试端到端的真·多智能体调研系统（边界隔离 + Orchestrator-Workers 闭环）。

    关键词「磁吸手机壳」不含工厂/出厂/1688/跨境等供应链信号，领域硬守卫
    仅放行全网工具并裁掉越权货源子任务，最终收敛生成研报。
    """
    from taozhi.orchestration.agent_system import (
        _OrchestratorDecisionSchema,
        _ProposedSubtaskSchema,
        _SubagentStepSchema,
    )

    mock_llm = MagicMock()
    orch_calls = {"n": 0}

    def fake_chat_json(*, prompt: str, schema=None, **kwargs):
        if schema is _OrchestratorDecisionSchema:
            orch_calls["n"] += 1
            if orch_calls["n"] == 1:
                return _OrchestratorDecisionSchema(
                    action="spawn_subagents",
                    thought="检索全网磁吸手机壳主流款式与用户评价",
                    outline=["市场概览", "竞品痛点", "选品建议"],
                    subtasks=[
                        _ProposedSubtaskSchema(
                            role="全网市场情报专员",
                            goal="采集磁吸手机壳全网口碑与价位",
                            allowed_tools=["search_web", "search_1688_wholesale"],  # 测试领域守卫裁掉越权 1688
                            seed_queries=["磁吸手机壳"],
                            channel_hint="web",
                        )
                    ],
                )
            return _OrchestratorDecisionSchema(
                action="finish",
                thought="已获得充分全网实证",
                outline=["市场概览", "竞品痛点", "选品建议"],
            )

        if "历史观测记录：\n[]" in prompt:
            return _SubagentStepSchema(
                action="call_tool",
                thought="使用授权的 search_web 检索全网口碑",
                tool_name="search_web",
                query_or_url="磁吸手机壳",
            )
        return _SubagentStepSchema(
            action="finish",
            thought="收集完毕",
            findings_summary="全网搜索发现：主流款式为超薄磁吸，热销价 39.9 元。",
            key_metrics=["热销价39.9元"],
        )

    mock_llm.chat_json.side_effect = fake_chat_json
    mock_llm.chat.return_value = (
        "# 磁吸支架手机壳电商市场调研报告\n\n## 1. 市场概览\n全网需求旺盛。\n\n## 2. 选品建议\n紧扣核心卖点。"
    )

    progress_stages = []

    def on_progress(stage: str, pct: int):
        progress_stages.append((stage, pct))

    with patch("taozhi.orchestration.agent_system.LLMClient", return_value=mock_llm):
        with patch("taozhi.orchestration.agent_system.search_web", return_value=[{"title": "磁吸壳测评", "snippet": "热销39.9元"}]):
            result = execute_autonomous_agents(keyword="磁吸手机壳", emit_event_fn=on_progress)

    assert "report" in result
    assert "磁吸支架手机壳电商市场调研报告" in result["report"]
    assert len(result["outline"]) == 3
    # 边界隔离：越权 1688 工具被领域守卫剥离，只保留 web 单渠道与 2 条审计跃迁（子智能体 + 合成专家）
    assert len(result["platforms"]) == 1
    assert result["platforms"][0]["name"] == "web"
    assert len(result["subagents"]) == 2
    assert len(progress_stages) > 0
    assert any(pct == 100 for _, pct in progress_stages)
