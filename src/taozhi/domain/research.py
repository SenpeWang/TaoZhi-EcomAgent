"""市场调研多智能体领域规则与安全护栏（Domain Layer，纯标准库实现）。

本模块不依赖任何 Web 框架、数据库、LangGraph、配置或大模型客户端，
只负责承载以下业务不变量（绝不替智能体硬编码执行步骤顺序）：
1. 调研意图与工具白名单边界（ResearchScope / enforce_tool_allowed）；
2. 多智能体并行预算与任务授权裁剪（ResearchBudget / authorize_subagent_tasks）；
3. 硬性停止与应急收敛判定（StopReason / evaluate_hard_stop）；
4. 运行期纠偏影响范围识别（SteeringImpact / classify_steering_impact）。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from .agent_catalog import agent_display_name
from .errors import Denied
from .ranking import tokenize_zh

WEB_TOOLS: Tuple[str, ...] = ("search_web", "fetch_web_page")
SOURCING_TOOLS: Tuple[str, ...] = ("search_1688_wholesale", "search_amazon")
SOURCE_TOOLS: Tuple[str, ...] = WEB_TOOLS + SOURCING_TOOLS
CLOCK_TOOL = "get_current_time"
ALL_RESEARCH_TOOLS: Tuple[str, ...] = SOURCE_TOOLS + (CLOCK_TOOL,)

_SOURCING_PATTERN = re.compile(
    r"工厂|出厂|批发|1688|货源|起订|供应商|供货|打样|工艺|亚马逊|amazon|跨境|比价|毛利|套利|全景|全面|底价",
    re.IGNORECASE,
)
_WEB_PATTERN = re.compile(
    r"差评|好评|痛点|消费者|买家|卖点|竞品|热销|评价|全网|网页|趋势|口碑"
)


class StopReason(str, Enum):
    """调研多智能体系统终止/流转原因枚举。"""

    CONTINUE = "continue"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    MAX_TURNS = "max_turns"


class ResearchStatus(str, Enum):
    """调研任务的运行状态；只保留进行中、报告合成与三个终态。"""

    RUNNING = "RUNNING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class AgentRunState(str, Enum):
    """审计轨迹中单个智能体的生命周期状态。"""

    SPAWNED = "SPAWNED"
    ANSWERING = "ANSWERING"
    COORDINATING = "COORDINATING"
    SYNTHESIZING = "SYNTHESIZING"
    COMPLETED = "COMPLETED"
    PARTIAL_OR_FAILED = "PARTIAL_OR_FAILED"


class ToolCaller(str, Enum):
    """工具调用者身份；能力策略按调用者而不是按固定角色绑定工具。"""

    ORCHESTRATOR = "orchestrator"
    QUICK_ANSWER = "quick_answer"
    RESEARCH_WORKER = "research_worker"
    SYNTHESIZER = "synthesizer"


@dataclass(frozen=True)
class ResearchScope:
    """调研业务边界与合法工具集。"""

    needs_sourcing: bool
    allowed_tools: Tuple[str, ...]
    forbidden_tools: Tuple[str, ...]


@dataclass(frozen=True)
class ResearchBudget:
    """多智能体系统硬性运行预算（数值由外层配置或调用方传入）。"""

    max_turns: int = 5
    max_parallel_subagents: int = 4
    max_subagent_steps: int = 3
    max_coordinator_tool_calls: int = 2
    timeout_seconds: float = 180.0


DEFAULT_OUTPUT_FORMAT = "findings_summary + key_metrics + gaps_or_errors 三段式结论"
DEFAULT_SOURCE_GUIDANCE = "优先官方/规格/平台原始页面，保留 URL 与数据时间"
DEFAULT_TASK_BOUNDARIES = "只覆盖本目标，不重复其他子任务，不补充未授权渠道"


@dataclass(frozen=True)
class SubagentTaskSpec:
    """主协调代理动态生成的单个子智能体派工简报。"""

    task_id: str
    role: str
    goal: str
    suggested_tools: Tuple[str, ...] = ()
    seed_queries: Tuple[str, ...] = ()
    channel_hint: str = "auto"
    output_format: str = DEFAULT_OUTPUT_FORMAT
    source_guidance: str = DEFAULT_SOURCE_GUIDANCE
    boundaries: str = DEFAULT_TASK_BOUNDARIES
    expected_evidence: str = "可核对的来源、关键数字与缺口"
    model_tier: str = "fast"


@dataclass(frozen=True)
class AuthorizedPlan:
    """经过领域边界与并发预算校验后的合法派工方案。"""

    tasks: Tuple[SubagentTaskSpec, ...]
    guard_rejections: Tuple[str, ...]


@dataclass(frozen=True)
class HardStopDecision:
    """领域硬性停止守卫的裁决结果。"""

    should_stop: bool
    force_synthesize: bool
    system_status: str
    stop_reason: StopReason
    rationale: str


@dataclass(frozen=True)
class SteeringImpact:
    """运行期动态干预（Steering）对既有事实池的参考失效范围。"""

    invalidate_web: bool
    invalidate_wholesale: bool


def classify_research_scope(
    user_query: str,
    steering_notes: Sequence[str] = (),
) -> ResearchScope:
    """根据用户诉求与实时干预指令识别允许调用的工具白名单与禁用工具。"""
    combined = " ".join([user_query or "", *steering_notes]).strip()
    needs_sourcing = bool(_SOURCING_PATTERN.search(combined))

    if needs_sourcing:
        allowed = ALL_RESEARCH_TOOLS
        forbidden: Tuple[str, ...] = ()
    else:
        allowed = WEB_TOOLS + (CLOCK_TOOL,)
        forbidden = SOURCING_TOOLS

    return ResearchScope(
        needs_sourcing=needs_sourcing,
        allowed_tools=allowed,
        forbidden_tools=forbidden,
    )


def tools_for_caller(caller: ToolCaller, scope: ResearchScope) -> Tuple[str, ...]:
    """返回调用者能力范围内的 MCP 工具；分析师不持有工具。"""
    if caller is ToolCaller.SYNTHESIZER:
        return ()
    return scope.allowed_tools


def enforce_tool_allowed(tool_name: str, scope: ResearchScope, *, caller: ToolCaller) -> None:
    """领域硬守卫：拦截未知工具、越界工具与不允许工具调用的角色。"""
    if tool_name not in ALL_RESEARCH_TOOLS:
        raise Denied(
            f"未知工具 {tool_name} 未在领域工具契约中登记，已被拒绝执行",
            "UNKNOWN_TOOL",
            403,
        )
    if caller is ToolCaller.SYNTHESIZER:
        raise Denied("研报分析师不允许调用工具，只能使用黑板证据", "TOOL_NOT_GRANTED", 403)
    if tool_name in scope.forbidden_tools or tool_name not in scope.allowed_tools:
        raise Denied(
            f"工具 {tool_name} 超出当前调研边界，已被领域守卫拦截",
            "TOOL_OUT_OF_SCOPE",
            403,
        )


def _task_overlap(
    left_goal: str,
    left_queries: Sequence[str],
    right_goal: str,
    right_queries: Sequence[str],
) -> bool:
    """判断两个子任务的目标与起步查询是否高度重叠。"""
    left_text = " ".join([left_goal or "", *left_queries])
    right_text = " ".join([right_goal or "", *right_queries])
    left_tokens = set(tokenize_zh(left_text))
    right_tokens = set(tokenize_zh(right_text))
    if left_tokens and right_tokens:
        jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        if jaccard >= 0.6:
            return True
    left_clean = re.sub(r"\s+", "", left_goal or "")
    right_clean = re.sub(r"\s+", "", right_goal or "")
    return bool(left_clean and right_clean) and difflib.SequenceMatcher(None, left_clean, right_clean).ratio() >= 0.85


def authorize_subagent_tasks(
    proposed: Sequence[SubagentTaskSpec],
    scope: ResearchScope,
    budget: ResearchBudget,
    history_goals: Sequence[str] = (),
) -> AuthorizedPlan:
    """对派工简报做去重、并发上限收敛；建议工具只保留 scope 内条目，不硬性限制。"""
    authorized: List[SubagentTaskSpec] = []
    rejections: List[str] = []
    seen_ids: set[str] = set()
    max_parallel = max(1, int(budget.max_parallel_subagents))
    history = [goal.strip() for goal in history_goals if goal and goal.strip()]

    for idx, task in enumerate(proposed):
        tid = (task.task_id or f"sub_{idx + 1}").strip()
        if tid in seen_ids:
            tid = f"{tid}_{idx + 1}"
        seen_ids.add(tid)
        goal = (task.goal or "收集与品类相关的客观市场事实").strip()
        queries = tuple(q.strip() for q in task.seed_queries if q and q.strip())

        if any(_task_overlap(goal, queries, previous, ()) for previous in history) or any(
            _task_overlap(goal, queries, item.goal, item.seed_queries) for item in authorized
        ):
            rejections.append(f"子任务 [{agent_display_name(task.role)}] 与已有任务目标重叠，已跳过")
            continue

        if len(authorized) >= max_parallel:
            rejections.append(f"子任务 [{agent_display_name(task.role)}] 超出单轮并发上限 {max_parallel}，本轮暂不派发")
            continue

        suggested = tuple(tool for tool in task.suggested_tools if tool in scope.allowed_tools)
        ignored = [tool for tool in task.suggested_tools if tool not in scope.allowed_tools]
        if ignored:
            rejections.append(f"子任务 [{agent_display_name(task.role)}] 的建议工具越界已忽略：{','.join(ignored)}")

        channel = task.channel_hint if task.channel_hint in {"web", "sourcing"} else (
            "sourcing" if scope.needs_sourcing else "web"
        )
        authorized.append(
            SubagentTaskSpec(
                task_id=tid,
                role=task.role,
                goal=goal,
                suggested_tools=suggested,
                seed_queries=queries,
                channel_hint=channel,
                output_format=task.output_format or DEFAULT_OUTPUT_FORMAT,
                source_guidance=task.source_guidance or DEFAULT_SOURCE_GUIDANCE,
                boundaries=task.boundaries or DEFAULT_TASK_BOUNDARIES,
                expected_evidence=task.expected_evidence or "可核对的来源、关键数字与缺口",
            )
        )

    return AuthorizedPlan(tasks=tuple(authorized), guard_rejections=tuple(rejections))


def evaluate_hard_stop(
    turn: int,
    elapsed_seconds: float,
    cancelled: bool,
    has_report: bool,
    evidence_count: int,
    budget: ResearchBudget,
) -> HardStopDecision:
    """评估硬停止条件（优先级：CANCELLED > COMPLETED > TIMEOUT > MAX_TURNS > CONTINUE）。

    注意：本函数不规定 Agent 的思考路径，仅守卫生命周期底线。
    """
    if cancelled:
        return HardStopDecision(
            should_stop=True,
            force_synthesize=False,
            system_status="CANCELLED",
            stop_reason=StopReason.CANCELLED,
            rationale="检测到任务取消请求，立即熔断终止后续协调与智能体调用。",
        )

    if has_report:
        return HardStopDecision(
            should_stop=True,
            force_synthesize=False,
            system_status="COMPLETED",
            stop_reason=StopReason.COMPLETED,
            rationale="终审研报已合成就绪，多智能体协同达成目标闭环。",
        )

    if budget.timeout_seconds > 0 and elapsed_seconds >= budget.timeout_seconds:
        if evidence_count > 0:
            return HardStopDecision(
                should_stop=True,
                force_synthesize=True,
                system_status="REPORTING",
                stop_reason=StopReason.TIMEOUT,
                rationale=(
                    f"全局耗时已达 {elapsed_seconds:.1f}s（上限 {budget.timeout_seconds:.1f}s），"
                    "停止新增探索并收敛现有证据生成研报。"
                ),
            )
        return HardStopDecision(
            should_stop=True,
            force_synthesize=False,
            system_status="TIMEOUT",
            stop_reason=StopReason.TIMEOUT,
            rationale=f"全局耗时已达上限（{budget.timeout_seconds:.1f}s）且无有效证据，终止调研任务。",
        )

    if turn >= max(1, int(budget.max_turns)):
        return HardStopDecision(
            should_stop=True,
            force_synthesize=True,
            system_status="REPORTING",
            stop_reason=StopReason.MAX_TURNS,
            rationale=f"主协调轮次已达上限 {budget.max_turns}，收敛黑板现有证据生成终审研报。",
        )

    return HardStopDecision(
        should_stop=False,
        force_synthesize=False,
        system_status="RUNNING",
        stop_reason=StopReason.CONTINUE,
        rationale="预算充足且未触发熔断，继续由 Lead Agent 自主决策。",
    )


def classify_steering_impact(instruction: str) -> SteeringImpact:
    """判定中途干预指令可能影响的业务维度（供主脑参考失效清理）。"""
    text = (instruction or "").strip()
    if not text:
        return SteeringImpact(False, False)

    hits_sourcing = bool(_SOURCING_PATTERN.search(text))
    hits_web = bool(_WEB_PATTERN.search(text))
    return SteeringImpact(
        invalidate_web=hits_web,
        invalidate_wholesale=hits_sourcing,
    )
