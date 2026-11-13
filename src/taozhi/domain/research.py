"""市场调研领域核心模型与停止策略规则（Domain Layer，纯标准库实现）。

本模块不依赖任何 Web 框架、数据库、LangGraph、配置或外部网络客户端，
集中承载调研意图边界判定、工具越权物理禁区、动态干预影响评估与停止收敛决策规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence, Tuple

from .errors import Denied

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
    """调研多智能体停止/流转原因枚举。"""

    CONTINUE = "continue"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    MAX_TURNS = "max_turns"


@dataclass(frozen=True)
class ResearchScope:
    """调研业务边界与工具权限范围。"""

    needs_web: bool
    needs_sourcing: bool
    has_explicit_sourcing: bool
    has_explicit_web: bool
    forbidden_tools: Tuple[str, ...]


@dataclass(frozen=True)
class SteeringImpact:
    """运行期动态干预（Steering）对既有事实池的失效影响。"""

    invalidate_web: bool
    invalidate_wholesale: bool
    invalidate_verification: bool


@dataclass(frozen=True)
class ResearchSnapshot:
    """用于停止与路由裁决的纯内存状态快照。"""

    turn: int
    max_turns: int
    elapsed_seconds: float
    timeout_seconds: float
    cancelled: bool
    scope: ResearchScope
    has_web: bool
    web_count: int
    has_wholesale: bool
    wholesale_count: int
    has_verification: bool
    has_report: bool
    web_retries: int = 0
    scanner_retries: int = 0
    max_empty_retries: int = 0


@dataclass(frozen=True)
class SupervisorDecision:
    """主管智能体的单步裁决结果。"""

    next_action: str
    system_status: str
    rationale: str
    stop_reason: StopReason


def classify_research_scope(
    user_query: str,
    steering_notes: Sequence[str] = (),
) -> ResearchScope:
    """根据用户诉求与干预指令识别调研边界与禁用工具清单。"""
    combined = " ".join([user_query or "", *steering_notes]).strip()
    has_explicit_sourcing = bool(_EXPLICIT_SOURCING_PATTERN.search(combined))
    needs_sourcing = bool(_SOURCING_PATTERN.search(combined))
    has_explicit_web = bool(_WEB_PATTERN.search(combined))
    needs_web = True  # 全网情报默认作为基础维度

    forbidden: Tuple[str, ...] = ()
    if not needs_sourcing:
        forbidden = ("search_1688_wholesale", "search_amazon")

    return ResearchScope(
        needs_web=needs_web,
        needs_sourcing=needs_sourcing,
        has_explicit_sourcing=has_explicit_sourcing,
        has_explicit_web=has_explicit_web,
        forbidden_tools=forbidden,
    )


def enforce_tool_allowed(tool_name: str, scope: ResearchScope) -> None:
    """领域硬守卫：禁止调研智能体调用超出当前业务边界的受限工具。"""
    if tool_name in scope.forbidden_tools:
        raise Denied(
            f"工具 {tool_name} 超出当前纯全网调研边界，已被领域守卫拦截",
            "TOOL_OUT_OF_SCOPE",
            403,
        )


def classify_steering_impact(instruction: str) -> SteeringImpact:
    """判定中途新增的干预指令应当使哪些已采事实失效重采。"""
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


def evaluate_next_step(snapshot: ResearchSnapshot) -> SupervisorDecision:
    """领域纯规则决策器：统一评估停止条件、边界隔离与下一步委派。

    优先级顺序：
    1. 外部取消信号 -> 立即熔断终止（CANCELLED）；
    2. 全局 Wall-Clock 超时 -> 若已有部分事实则紧急收敛出报告，否则直接终止；
    3. 报告已就绪 -> 正常完成闭环（COMPLETED）；
    4. 轮次达到 max_turns 硬上限 -> 强制收敛出报告或结束（MAX_TURNS）；
    5. 按边界范围与事实完备度动态调度专职智能体（含空采集可控重抓）。
    """
    if snapshot.cancelled:
        return SupervisorDecision(
            next_action="FINISH",
            system_status="CANCELLED",
            rationale="检测到任务取消请求，立即终止后续调研与模型调用。",
            stop_reason=StopReason.CANCELLED,
        )

    if snapshot.timeout_seconds > 0 and snapshot.elapsed_seconds >= snapshot.timeout_seconds:
        if not snapshot.has_report and (snapshot.has_web or snapshot.has_wholesale):
            return SupervisorDecision(
                next_action="reporter_agent",
                system_status="REPORTING",
                rationale=(
                    f"全局耗时已达 {snapshot.elapsed_seconds:.1f}s（上限 {snapshot.timeout_seconds:.1f}s），"
                    "主脑停止继续采集并紧急收敛当前已获事实生成研报。"
                ),
                stop_reason=StopReason.TIMEOUT,
            )
        return SupervisorDecision(
            next_action="FINISH",
            system_status="COMPLETED" if snapshot.has_report else "TIMEOUT",
            rationale=f"全局耗时已达上限（{snapshot.timeout_seconds:.1f}s），终止调研任务。",
            stop_reason=StopReason.TIMEOUT,
        )

    if snapshot.has_report:
        return SupervisorDecision(
            next_action="FINISH",
            system_status="COMPLETED",
            rationale="终审研报已编排就绪，多智能体协同达成目标闭环。",
            stop_reason=StopReason.COMPLETED,
        )

    if snapshot.turn >= snapshot.max_turns:
        return SupervisorDecision(
            next_action="reporter_agent",
            system_status="REPORTING",
            rationale="决策轮次已达阈值，主脑立即指示 reporter_agent 依据当前已知事实紧急收敛生成研报。",
            stop_reason=StopReason.MAX_TURNS,
        )

    scope = snapshot.scope
    web_incomplete = (not snapshot.has_web) or (
        snapshot.web_count == 0 and snapshot.web_retries < snapshot.max_empty_retries
    )
    wholesale_incomplete = scope.needs_sourcing and (
        (not snapshot.has_wholesale)
        or (snapshot.wholesale_count == 0 and snapshot.scanner_retries < snapshot.max_empty_retries)
    )

    if scope.has_explicit_sourcing and wholesale_incomplete:
        return SupervisorDecision(
            next_action="scanner_agent",
            system_status="COLLECTING",
            rationale="用户聚焦一手出厂底价与跨境零售对标，主脑自主委派 scanner_agent 深入 1688 与 Amazon 探查。",
            stop_reason=StopReason.CONTINUE,
        )

    if scope.has_explicit_web and web_incomplete:
        return SupervisorDecision(
            next_action="web_agent",
            system_status="EXPLORING",
            rationale="用户聚焦全网消费者口碑与行业热点，主脑自主委派 web_agent 检索全网网页情报。",
            stop_reason=StopReason.CONTINUE,
        )

    if web_incomplete:
        return SupervisorDecision(
            next_action="web_agent",
            system_status="EXPLORING",
            rationale="尚未掌握全网零售与用户痛点大盘，主脑自主委派 web_agent 探索全网网页情报。",
            stop_reason=StopReason.CONTINUE,
        )

    if wholesale_incomplete:
        return SupervisorDecision(
            next_action="scanner_agent",
            system_status="COLLECTING",
            rationale="已掌握全网痛点，尚缺真实供应链出厂价与海外对标，主脑自主委派 scanner_agent 探查 1688 与 Amazon 双渠道。",
            stop_reason=StopReason.CONTINUE,
        )

    if not scope.needs_sourcing:
        return SupervisorDecision(
            next_action="reporter_agent",
            system_status="REPORTING",
            rationale="用户需求聚焦全网口碑与舆情卖点，未涉及供应链与跨境比价，主脑严格遵守边界隔离跳过货源探查，直接委派 reporter_agent 编制分析研报。",
            stop_reason=StopReason.CONTINUE,
        )

    if not snapshot.has_verification:
        return SupervisorDecision(
            next_action="verifier_agent",
            system_status="VERIFYING",
            rationale="两端数据已就绪，主脑自主委派 verifier_agent 交叉比对两端价差并核实真实毛利与虚假营销。",
            stop_reason=StopReason.CONTINUE,
        )

    return SupervisorDecision(
        next_action="reporter_agent",
        system_status="REPORTING",
        rationale="所有业务事实已完成交叉核验与去伪存真，主脑自主委派 reporter_agent 编制终审全景研报。",
        stop_reason=StopReason.CONTINUE,
    )
