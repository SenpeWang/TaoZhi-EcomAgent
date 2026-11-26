"""调研运行时与测评中心的契约回归：能力边界、Prompt 快照与任务板协议。"""
from __future__ import annotations

import pytest

from evals.runtime_snapshot import algorithm_snapshot
from taozhi.domain.errors import Denied
from taozhi.domain.research import (
    ResearchScope,
    ToolCaller,
    enforce_tool_allowed,
    tools_for_caller,
)


def _scope(allowed, forbidden=()):
    return ResearchScope(needs_sourcing=False, allowed_tools=tuple(allowed), forbidden_tools=tuple(forbidden))


def test_tool_caller_capability_matrix():
    """协调员/速答/研究 Worker 可按 scope 调用，分析师无工具。"""
    scope = _scope(("search_web", "get_current_time"))
    assert tools_for_caller(ToolCaller.ORCHESTRATOR, scope) == scope.allowed_tools
    assert tools_for_caller(ToolCaller.QUICK_ANSWER, scope) == scope.allowed_tools
    assert tools_for_caller(ToolCaller.RESEARCH_WORKER, scope) == scope.allowed_tools
    assert tools_for_caller(ToolCaller.SYNTHESIZER, scope) == ()
    enforce_tool_allowed("search_web", scope, caller=ToolCaller.QUICK_ANSWER)
    with pytest.raises(Denied) as exc:
        enforce_tool_allowed("search_web", scope, caller=ToolCaller.SYNTHESIZER)
    assert exc.value.code == "TOOL_NOT_GRANTED"


def test_algorithm_snapshot_includes_prompt_catalogs():
    """算法快照必须可复现运行期与评测期 Prompt 版本。"""
    catalogs = algorithm_snapshot()["prompt_catalogs"]
    assert set(catalogs) == {"research", "rag", "orchestration", "eval"}
    assert all(item["checksum"] for item in catalogs.values())


def test_task_board_brief_contract_is_serializable():
    """任务板简报只接受 JSON 原生字段，便于持久化与回放。"""
    from taozhi.application.research.task_board import _brief
    from taozhi.domain.research import SubagentTaskSpec

    brief = _brief(
        SubagentTaskSpec(
            task_id="t1",
            role="research.market_intelligence",
            goal="查口碑",
            suggested_tools=("search_web",),
            seed_queries=("保温杯",),
            output_format="三段式",
            source_guidance="官方页面",
            boundaries="只查口碑",
            expected_evidence="来源与数字",
        )
    )
    assert brief["task_id"] == "t1"
    assert brief["suggested_tools"] == ["search_web"]
    assert brief["write_scopes"] == []

def test_quick_and_coordinator_judges_use_roles():
    """新路由判定器只依据真实角色/证据，不依赖虚构字段。"""
    from evals.agent_eval.harness import AgentRunResult
    from evals.agent_eval.judges import CoordinatorToolJudge, QuickAnswerJudge

    result = AgentRunResult(
        report="这是一个简洁回答。",
        system_status="COMPLETED",
        subagent_roles=["research.quick_answer"],
        evidence_roles=["research.orchestrator", "research.quick_answer"],
    )
    assert QuickAnswerJudge(True).evaluate(result).passed
    assert QuickAnswerJudge(False).evaluate(result).passed is False
    assert CoordinatorToolJudge(True).evaluate(result).passed
    assert CoordinatorToolJudge(False).evaluate(result).passed is False
