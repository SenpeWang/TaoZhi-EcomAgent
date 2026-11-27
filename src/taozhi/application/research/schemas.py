"""市场调研多智能体结构化输出契约与全局黑板状态定义。"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class _ProposedSubtaskSchema(BaseModel):
    """协调员动态派发子任务的结构化规约。"""
    role: Literal[
        "research.market_intelligence", "research.sourcing",
        "research.amazon_benchmark", "research.generalist",
    ] = Field(description="执行角色编号：市场情报、采购、亚马逊比价或综合调研")
    goal: str = Field(description="本轮指派给该子智能体的明确调查目标")
    suggested_tools: List[str] = Field(default_factory=list, description="建议工具，仅作参考，不再硬性限制调用")
    seed_queries: List[str] = Field(default_factory=list, description="建议起步检索关键词或网址列表")
    channel_hint: Literal["web", "sourcing", "auto"] = "auto"
    output_format: str = Field(default="findings_summary + key_metrics + gaps_or_errors 三段式结论", description="期望交付格式")
    source_guidance: str = Field(default="优先官方/规格/平台原始页面，保留 URL 与数据时间", description="来源要求")
    boundaries: str = Field(default="只覆盖本目标，不重复其他子任务，不补充未授权渠道", description="任务边界")
    expected_evidence: str = Field(default="可核对的来源、关键数字与缺口", description="预期证据")
    model_tier: Literal["fast", "strong"] = Field(default="fast", description="该子任务使用的模型层级")


class _OrchestratorDecisionSchema(BaseModel):
    """协调员单轮调度决策的结构化输出。"""
    action: Literal["spawn_subagents", "quick_answer", "call_tool", "finish"] = Field(description="spawn_subagents 派发研究子智能体；quick_answer 派速答 Worker；call_tool 直接调用一个轻量工具；finish 收敛写研报")
    thought: str = Field(description="根据已有证据、信息缺口与更新要求做出的调度理由")
    outline: List[str] = Field(default_factory=list, description="3-4个核心研报章节维度")
    invalidate_evidence_ids: List[str] = Field(default_factory=list, description="需要作废的既有证据 ID（如被中途 Steering 推翻）")
    subtasks: List[_ProposedSubtaskSchema] = Field(default_factory=list, description="当 action=spawn_subagents 时动态派发的并行子任务列表")
    tool_name: str = Field(default="", description="当 action=call_tool 时调用的工具名")
    query_or_url: str = Field(default="", description="当 action=call_tool 时的检索词或目标 URL")
    timezone: str | None = Field(default=None, description="读取时间时的 IANA 时区")


class _QuickAnswerSchema(BaseModel):
    """速答 Worker 的一次工具决策或直接回答。"""

    action: Literal["answer", "call_tool"] = Field(description="answer 直接回答；call_tool 调用一次授权工具后再回答")
    thought: str = Field(description="本轮判断依据")
    tool_name: str = Field(default="", description="call_tool 时的工具名，仅限 search_web、fetch_web_page、get_current_time")
    query_or_url: str = Field(default="", description="call_tool 时的检索词或目标 URL")
    timezone: str | None = Field(default=None, description="读取时间时的 IANA 时区")
    answer: str = Field(default="", description="action=answer 时的简洁回答；资料不足时如实说明")
    gaps: List[str] = Field(default_factory=list, description="无法确认或需要转研究 Worker 的缺口")


class _SubagentStepSchema(BaseModel):
    """研究子智能体单步工具调用或交卷决策的结构化输出。"""
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
    task_id: str
    agent_type: str
    state_from: str
    state_to: str
    decision_reason: str
    tools: List[AuditableToolRecord]
    elapsed: float
    scratch_files: List[str]


class EvidenceRecord(TypedDict):
    """子智能体回写黑板的结构化证据包。"""

    evidence_id: str
    agent_id: str
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


class SubagentBrief(TypedDict):
    """协调员向 Worker 下发的派工简报（星型黑板，非 peer-to-peer）。"""

    task_id: str
    role: str
    goal: str
    output_format: str
    source_guidance: str
    boundaries: str
    suggested_tools: List[str]
    seed_queries: List[str]
    expected_evidence: str


class EvidenceEnvelope(TypedDict):
    """Worker 回写黑板的证据信封，由协调员读取，不直接广播给其他 Worker。"""

    task_id: str
    agent_id: str
    role: str
    channel: str
    status: str
    summary: str
    key_metrics: List[str]
    gaps_or_errors: List[str]
    tool_counts: Dict[str, int]


class WorkerResult(TypedDict):
    """Worker 终态：证据信封、工具审计与耗时。"""

    agent_id: str
    role: str
    status: str
    evidence: EvidenceEnvelope
    tools: List[AuditableToolRecord]
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
    decision_reason: str
    turn_count: int
    max_turns: int
    max_parallel_subagents: int
    max_subagent_steps: int
    max_coordinator_tool_calls: int
    coordinator_tool_calls: int
    started_at: float
    timeout_seconds: float
    cancelled: bool
    stop_reason: str

    outline: List[str]
    pending_tasks: List[Dict[str, Any]]
    guard_rejections: List[str]
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
