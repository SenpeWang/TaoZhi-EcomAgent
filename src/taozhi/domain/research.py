"""市场调研多智能体领域规则与安全护栏（Domain Layer，纯标准库实现）。

本模块不依赖任何 Web 框架、数据库、LangGraph、配置或大模型客户端，
只负责承载以下业务不变量（绝不替智能体硬编码执行步骤顺序）：
1. 调研意图与工具白名单边界（ResearchScope / enforce_tool_allowed）；
2. 多智能体并行预算与任务授权裁剪（ResearchBudget / authorize_subagent_tasks）；
3. 硬性停止与应急收敛判定（StopReason / evaluate_hard_stop）；
4. 运行期纠偏影响范围识别（SteeringImpact / classify_steering_impact）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from .errors import Denied

WEB_TOOLS: Tuple[str, ...] = ("search_web", "fetch_web_page")
SOURCING_TOOLS: Tuple[str, ...] = ("search_1688_wholesale", "search_amazon")
ALL_RESEARCH_TOOLS: Tuple[str, ...] = WEB_TOOLS + SOURCING_TOOLS

_SOURCING_PATTERN = re.compile(
    r"工厂|出厂|批发|1688|货源|起订|供应商|供货|打样|工艺|亚马逊|amazon|跨境|比价|毛利|套利|全景|全面|底价",
    re.IGNORECASE,
)
_EXPLICIT_SOURCING_PATTERN = re.compile(
    r"工厂|出厂|批发|1688|货源|起订|供应商|供货|打样|工艺|亚马逊|amazon|跨境",
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


@dataclass(frozen=True)
class ResearchScope:
    """调研业务边界与合法工具集。"""

    needs_web: bool
    needs_sourcing: bool
    has_explicit_sourcing: bool
    has_explicit_web: bool
    allowed_tools: Tuple[str, ...]
    forbidden_tools: Tuple[str, ...]


@dataclass(frozen=True)
class ResearchBudget:
    """多智能体系统硬性运行预算（数值由外层配置或调用方传入）。"""

    max_turns: int = 5
    max_parallel_subagents: int = 4
    max_subagent_steps: int = 3
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class SubagentTaskSpec:
    """主协调代理动态生成的单个子智能体派工契约。"""

    task_id: str
    role: str
    goal: str
    allowed_tools: Tuple[str, ...]
    seed_queries: Tuple[str, ...]
    channel_hint: str = "general"


@dataclass(frozen=True)
class AuthorizedPlan:
    """经过领域边界与并发预算校验后的合法派工方案。"""

    tasks: Tuple[SubagentTaskSpec, ...]
    rejected_reasons: Tuple[str, ...]


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
    invalidate_verification: bool


def classify_research_scope(
    user_query: str,
    steering_notes: Sequence[str] = (),
) -> ResearchScope:
    """根据用户诉求与实时干预指令识别允许调用的工具白名单与禁用工具。"""
    combined = " ".join([user_query or "", *steering_notes]).strip()
    has_explicit_sourcing = bool(_EXPLICIT_SOURCING_PATTERN.search(combined))
    needs_sourcing = bool(_SOURCING_PATTERN.search(combined))
    has_explicit_web = bool(_WEB_PATTERN.search(combined))
    needs_web = True

    if needs_sourcing:
        allowed = ALL_RESEARCH_TOOLS
        forbidden: Tuple[str, ...] = ()
    else:
        allowed = WEB_TOOLS
        forbidden = SOURCING_TOOLS

    return ResearchScope(
        needs_web=needs_web,
        needs_sourcing=needs_sourcing,
        has_explicit_sourcing=has_explicit_sourcing,
        has_explicit_web=has_explicit_web,
        allowed_tools=allowed,
        forbidden_tools=forbidden,
    )


def enforce_tool_allowed(
    tool_name: str,
    scope: ResearchScope,
    task_tools: Optional[Sequence[str]] = None,
) -> None:
    """领域硬守卫：拦截超出全局业务边界或未授予当前子智能体的工具调用。"""
    if tool_name not in ALL_RESEARCH_TOOLS:
        raise Denied(
            f"未知工具 {tool_name} 未在领域工具契约中登记，已被拒绝执行",
            "UNKNOWN_TOOL",
            403,
        )
    if tool_name in scope.forbidden_tools or tool_name not in scope.allowed_tools:
        raise Denied(
            f"工具 {tool_name} 超出当前调研边界，已被领域守卫拦截",
            "TOOL_OUT_OF_SCOPE",
            403,
        )
    if task_tools is not None and tool_name not in task_tools:
        raise Denied(
            f"工具 {tool_name} 未授权给当前子智能体角色，已被领域守卫拦截",
            "TOOL_NOT_GRANTED",
            403,
        )


def authorize_subagent_tasks(
    proposed: Sequence[SubagentTaskSpec],
    scope: ResearchScope,
    budget: ResearchBudget,
) -> AuthorizedPlan:
    """对主协调代理规划的并行子任务执行权限裁剪与并发上限收敛。"""
    authorized: List[SubagentTaskSpec] = []
    rejected: List[str] = []
    seen_ids: set[str] = set()
    max_parallel = max(1, int(budget.max_parallel_subagents))

    for idx, task in enumerate(proposed):
        tid = (task.task_id or f"sub_{idx + 1}").strip()
        if tid in seen_ids:
            tid = f"{tid}_{idx + 1}"
        seen_ids.add(tid)

        legal_tools: List[str] = []
        blocked_tools: List[str] = []
        for tool in task.allowed_tools:
            if tool in scope.allowed_tools and tool not in legal_tools:
                legal_tools.append(tool)
            elif tool not in legal_tools:
                blocked_tools.append(tool)

        if blocked_tools:
            rejected.append(f"子任务 [{task.role}] 请求的越界工具 {','.join(blocked_tools)} 已被领域守卫移除")

        if not legal_tools:
            rejected.append(f"子任务 [{task.role}] 无合法可用工具（当前允许: {','.join(scope.allowed_tools)}），整单驳回")
            continue

        if len(authorized) >= max_parallel:
            rejected.append(f"子任务 [{task.role}] 超出单轮并发上限 {max_parallel}，本轮暂不派发")
            continue

        queries = tuple(q.strip() for q in task.seed_queries if q and q.strip())
        authorized.append(
            SubagentTaskSpec(
                task_id=tid,
                role=(task.role or "市场调研专员").strip(),
                goal=(task.goal or "收集与品类相关的客观市场事实").strip(),
                allowed_tools=tuple(legal_tools),
                seed_queries=queries,
                channel_hint=task.channel_hint or (
                    "sourcing" if any(t in SOURCING_TOOLS for t in legal_tools) else "web"
                ),
            )
        )

    return AuthorizedPlan(tasks=tuple(authorized), rejected_reasons=tuple(rejected))


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
        return SteeringImpact(False, False, False)

    hits_sourcing = bool(_SOURCING_PATTERN.search(text))
    hits_web = bool(_WEB_PATTERN.search(text))
    return SteeringImpact(
        invalidate_web=hits_web,
        invalidate_wholesale=hits_sourcing,
        invalidate_verification=True,
    )
