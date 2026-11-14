"""测试多智能体上下文隔离、网络搜索工具与端到端 MCP 多智能体调研系统。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from taozhi.core.agent import SubagentContext, WorkspaceMode
from taozhi.core.tools.search_web import search_web
from taozhi.orchestration.agent_system import execute_autonomous_agents


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

    p1 = ctx.write_scratch("raw_data.json", '{"items": [1, 2, 3]}')
    assert p1.exists()
    assert str(p1) in ctx.get_scratch_files()

    p2 = ctx.write_artifact("report.md", "# 交付报告")
    assert p2.exists()
    assert str(p2) in ctx.get_artifacts()

    ctx.cleanup()
    assert not ctx.scratch_dir.exists()
    assert ctx.artifacts_dir.exists()


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


def test_full_multi_agent_research_flow():
    """测试端到端的真·多智能体调研系统（领域守卫 + MCP tools/call + Orchestrator-Workers 闭环）。

    关键词「磁吸手机壳」不含工厂/出厂/1688/跨境等供应链信号，领域硬守卫
    仅放行全网 MCP 工具并裁掉越权货源子任务，最终收敛生成研报。
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
                            allowed_tools=["search_web", "search_1688_wholesale"],
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
                thought="通过 MCP search_web 检索全网口碑",
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
        with patch(
            "taozhi.infrastructure.mcp.server.search_web",
            return_value=[{"title": "磁吸壳测评", "snippet": "热销39.9元"}],
        ):
            result = execute_autonomous_agents(keyword="磁吸手机壳", emit_event_fn=on_progress)

    assert "report" in result
    assert "磁吸支架手机壳电商市场调研报告" in result["report"]
    assert len(result["outline"]) == 3
    assert len(result["platforms"]) == 1
    assert result["platforms"][0]["name"] == "web"
    assert len(result["subagents"]) == 2
    assert len(progress_stages) > 0
    assert any(pct == 100 for _, pct in progress_stages)
