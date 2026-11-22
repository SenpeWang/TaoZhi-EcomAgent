"""市场调研多智能体结构化输出契约与全局黑板状态定义。"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class _ProposedSubtaskSchema(BaseModel):
    role: Literal[
        "research.market_intelligence", "research.sourcing",
        "research.competitive_pricing", "research.subagent_worker",
    ] = Field(description="执行角色编号：市场情报、采购、竞品比价或综合调研")
    goal: str = Field(description="本轮指派给该子智能体的明确调查目标")
    allowed_tools: List[str] = Field(description="授权工具名列表，只能从当前允许的白名单中选择")
    seed_queries: List[str] = Field(default_factory=list, description="建议起步检索关键词或网址列表")
    channel_hint: Literal["web", "sourcing", "verification", "general"] = "general"


class _OrchestratorDecisionSchema(BaseModel):
    action: Literal["spawn_subagents", "finish"] = Field(description="spawn_subagents 派发子智能体；finish 证据充分直接收敛写研报")
    thought: str = Field(description="根据已有证据、信息缺口与更新要求做出的调度理由")
    outline: List[str] = Field(default_factory=list, description="3-4个核心研报章节维度")
    invalidate_evidence_ids: List[str] = Field(default_factory=list, description="需要作废的既有证据 ID（如被中途 Steering 推翻）")
    subtasks: List[_ProposedSubtaskSchema] = Field(default_factory=list, description="当 action=spawn_subagents 时动态派发的并行子任务列表")


class _SubagentStepSchema(BaseModel):
    action: Literal["call_tool", "finish"] = Field(description="call_tool 调用授权工具探索；finish 证据足够交卷")
    thought: str = Field(description="当前观察后的简短推理，说明为什么换词检索、深抓链接或可以结束")
    tool_name: str = Field(default="", description="当 action=call_tool 时的工具名")
    query_or_url: str = Field(default="", description="当 action=call_tool 时的搜索词或 fetch_web_page 目标 URL")
    timezone: str | None = Field(default=None, description="读取当前时间时使用的 IANA 时区，省略则用 Asia/Shanghai")
    findings_summary: str = Field(default="", description="当 action=finish 时基于工具实测结果的客观结论摘要")
    key_metrics: List[str] = Field(default_factory=list, description="从工具结果直接提取的关键数据事实（含价格/起订量/销量/痛点，严禁编造）")
    gaps_or_errors: List[str] = Field(default_factory=list, description="客观存在的空结果、抓包失败或尚待补充的缺口")


class AuditableToolRecord(TypedDict):
    """单次工具调用的客观审计记录。"""

    tool_name: str
    parameters: Dict[str, Any]
    dispatched: bool
    status: str
    result_summary: str
    data_count: int
    elapsed: float


class AuditableAgentTransition(TypedDict):
    """子智能体生命周期与状态跃迁审计记录。"""

    agent_id: str
    role: str
    type_name: str
    state_from: str
    state_to: str
    decision_rationale: str
    tools: List[AuditableToolRecord]
    elapsed: float
    scratch_files: List[str]


class EvidenceRecord(TypedDict):
    """子智能体回写黑板的结构化证据包。"""

    evidence_id: str
    subagent_id: str
    role: str
    channel: str
    goal: str
    summary: str
    key_metrics: List[str]
    gaps_or_errors: List[str]
    platform_items: Dict[str, List[Dict[str, Any]]]
    time_references: List[str]
    tool_counts: Dict[str, int]
    status: str
    elapsed: float


def _keep_last_str(left: str, right: str) -> str:
    """并发子分支合流时保留最新非空字符串（兼容 LangGraph 并发 Send Superstep）。"""
    return right or left


class AutonomousAgentState(TypedDict):
    """多智能体系统在状态图之间流转的全局黑板状态（支持并发增量合并）。"""

    job_id: str
    tenant_id: str
    run_version: int
    conversation_id: str
    hook_run_id: str
    user_query: str
    keyword: str

    steering_inbox: Annotated[List[str], operator.add]
    steering_event_ids: Annotated[List[str], operator.add]
    processed_steering_count: int

    system_status: str
    next_action: str
    current_rationale: str
    turn_count: int
    max_turns: int
    max_parallel_subagents: int
    max_subagent_steps: int
    started_at: float
    timeout_seconds: float
    cancelled: bool
    stop_reason: str

    outline: List[str]
    pending_tasks: List[Dict[str, Any]]
    guard_notices: List[str]
    evidence_pool: Annotated[List[EvidenceRecord], operator.add]
    invalidated_evidence_ids: Annotated[List[str], operator.add]

    audit_trail: Annotated[List[AuditableAgentTransition], operator.add]
    active_llm_endpoint: Annotated[str, _keep_last_str]
    llm_route_history: Annotated[List[Dict[str, Any]], operator.add]
    artifacts: Annotated[List[str], operator.add]
    report: str


def active_evidence(state: AutonomousAgentState) -> List[EvidenceRecord]:
    """筛选尚未被动态干预作废的有效黑板证据包。"""
    invalidated = set(state.get("invalidated_evidence_ids") or [])
    return [ev for ev in (state.get("evidence_pool") or []) if ev.get("evidence_id") not in invalidated]
