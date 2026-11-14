"""电商市场调研自治多智能体系统（Application 编排层，Orchestrator–Workers 架构）。

核心架构原则：
1. 严格遵守四层单向依赖：仅依赖 domain 领域守卫与 infrastructure 底层工具/AI 能力，
   绝不反向依赖 interface 层（server/api/worker）或直连数据库；
2. 真·多智能体系统（非固定步骤 Workflow）：
   - Lead Research Agent（主协调代理）基于黑板证据与中途 Steering 动态组队（spawn_subagents）；
   - LangGraph Send 实现无冲突并行扇出（Parallel Fan-out / Fan-in Superstep）；
   - 每个受派 Subagent 独立运行有界 ReAct 工具探索循环（search_web / fetch_web_page / 1688 / Amazon）；
   - 领域层 ResearchScope / ResearchBudget / authorize_subagent_tasks / evaluate_hard_stop
     硬锁越权工具、并发上限、全局超时、最大轮次与取消熔断；
3. 真流式实时事件广播（graph.stream + 运行时线程安全回调注册表），支持取消止损与动态干预。
"""
from __future__ import annotations

import json
import operator
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from ..ai.client import LLMClient, ModelTier
from ..core.agent.context import ArtifactMetadata, SubagentContext
from ..core.memory.service import get_memory_store
from ..core.tools.outcome import ToolOutcome
from ..core.tools.search_web import extract_search_keyword
from ..domain.errors import Denied
from ..domain.research import (
    ResearchBudget,
    ResearchScope,
    SOURCING_TOOLS,
    StopReason,
    SubagentTaskSpec,
    WEB_TOOLS,
    authorize_subagent_tasks,
    classify_research_scope,
    classify_steering_impact,
    enforce_tool_allowed,
    evaluate_hard_stop,
)
from ..infrastructure.mcp.adapter import McpClientAdapter
from ..infrastructure.mcp.server import default_mcp_server


# ============================================================================
# 1. 运行时回调注册表（不进入 LangGraph State，兼容 PostgresSaver 与多线程 Send）
# ============================================================================

@dataclass
class _RuntimeHooks:
    check_cancel_fn: Optional[Callable[[], bool]] = None
    fetch_steering_fn: Optional[Callable[[], List[str]]] = None
    emit_fn: Optional[Callable[[str, int], None]] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _progress: int = 0


_HOOKS_LOCK = threading.Lock()
_HOOKS_BY_RUN_ID: Dict[str, _RuntimeHooks] = {}


def _get_hooks(hook_run_id: str) -> _RuntimeHooks:
    if not hook_run_id:
        return _RuntimeHooks()
    with _HOOKS_LOCK:
        return _HOOKS_BY_RUN_ID.get(hook_run_id) or _RuntimeHooks()


def _emit_progress(hooks: _RuntimeHooks, stage: str, target_pct: Optional[int] = None) -> None:
    """线程安全地单调推进并广播阶段事件。"""
    with hooks._lock:
        if target_pct is not None:
            hooks._progress = max(hooks._progress, min(100, int(target_pct)))
        pct = hooks._progress
    if hooks.emit_fn is not None:
        try:
            hooks.emit_fn(stage, pct)
        except Exception:
            pass


def _bump_progress(hooks: _RuntimeHooks, delta: int, cap: int, stage: str) -> None:
    """在指定上限内单调递增进度并推送事件。"""
    with hooks._lock:
        if hooks._progress < cap:
            hooks._progress = min(cap, hooks._progress + delta)
        pct = hooks._progress
    if hooks.emit_fn is not None:
        try:
            hooks.emit_fn(stage, pct)
        except Exception:
            pass


def _is_cancelled(hooks: _RuntimeHooks, state_cancelled: bool = False) -> bool:
    if state_cancelled or hooks.cancel_event.is_set():
        hooks.cancel_event.set()
        return True
    if hooks.check_cancel_fn is not None:
        try:
            if hooks.check_cancel_fn():
                hooks.cancel_event.set()
                return True
        except Exception:
            pass
    return False


# ============================================================================
# 2. 结构化智能体决策契约（Pydantic Schemas for LLMClient.chat_json）
# ============================================================================

class _ProposedSubtaskSchema(BaseModel):
    role: str = Field(description="子智能体角色名称，例如 全网痛点研究员 / 1688货源采购专员 / Amazon对标专员")
    goal: str = Field(description="本轮指派给该子智能体的明确调查目标")
    allowed_tools: List[str] = Field(description="授权工具名列表，只能从当前允许的白名单中选择")
    seed_queries: List[str] = Field(default_factory=list, description="建议起步检索关键词或网址列表")
    channel_hint: Literal["web", "sourcing", "verification", "general"] = "general"


class _OrchestratorDecisionSchema(BaseModel):
    action: Literal["spawn_subagents", "finish"] = Field(description="spawn_subagents 派发子智能体；finish 证据充分直接收敛写研报")
    thought: str = Field(description="主脑基于当前黑板证据、缺口与纠偏指令做出的决策理由")
    outline: List[str] = Field(default_factory=list, description="3-4个核心研报章节维度")
    invalidate_evidence_ids: List[str] = Field(default_factory=list, description="需要作废的既有证据 ID（如被中途 Steering 推翻）")
    subtasks: List[_ProposedSubtaskSchema] = Field(default_factory=list, description="当 action=spawn_subagents 时动态派发的并行子任务列表")


class _SubagentStepSchema(BaseModel):
    action: Literal["call_tool", "finish"] = Field(description="call_tool 调用授权工具探索；finish 证据足够交卷")
    thought: str = Field(description="当前观察后的简短推理，说明为什么换词检索、深抓链接或可以结束")
    tool_name: str = Field(default="", description="当 action=call_tool 时的工具名")
    query_or_url: str = Field(default="", description="当 action=call_tool 时的搜索词或 fetch_web_page 目标 URL")
    findings_summary: str = Field(default="", description="当 action=finish 时基于工具实测结果的客观结论摘要")
    key_metrics: List[str] = Field(default_factory=list, description="从工具结果直接提取的关键数据事实（含价格/起订量/销量/痛点，严禁编造）")
    gaps_or_errors: List[str] = Field(default_factory=list, description="客观存在的空结果、抓包失败或尚待补充的缺口")


# ============================================================================
# 3. 强类型可审计状态与黑板数据结构
# ============================================================================

class AuditableToolRecord(TypedDict):
    """单次工具调用的客观审计记录。"""

    tool_name: str
    parameters: Dict[str, Any]
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
    tool_counts: Dict[str, int]
    status: str
    elapsed: float


class AutonomousAgentState(TypedDict):
    """多智能体系统在状态图之间流转的全局黑板状态（支持并发增量合并）。"""

    job_id: str
    tenant_id: str
    run_version: int
    conversation_id: str
    hook_run_id: str
    user_query: str
    keyword: str

    # 动态中途干预（Steering）信箱
    steering_inbox: Annotated[List[str], operator.add]
    processed_steering_count: int

    # 硬预算与运行期控制信标（仅主协调节点/终态节点可写）
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

    # 主脑规划与黑板证据（并发子任务仅写累加通道，零写冲突）
    outline: List[str]
    pending_tasks: List[Dict[str, Any]]
    guard_notices: List[str]
    evidence_pool: Annotated[List[EvidenceRecord], operator.add]
    invalidated_evidence_ids: Annotated[List[str], operator.add]

    # 可审计轨迹与最终交付物
    audit_trail: Annotated[List[AuditableAgentTransition], operator.add]
    artifacts: Annotated[List[str], operator.add]
    report: str


# ============================================================================
# 4. 辅助函数：干预收集、工具派发与 Fallback 构建
# ============================================================================

def _collect_new_steering(state: AutonomousAgentState, hooks: _RuntimeHooks) -> tuple[List[str], List[str]]:
    """拉取并去重外部动态干预指令，返回 (delta, 完整 inbox)。"""
    existing = list(state.get("steering_inbox") or [])
    delta: List[str] = []
    if hooks.fetch_steering_fn is not None:
        try:
            remote_items = list(hooks.fetch_steering_fn() or [])
            seen = set(existing)
            for item in remote_items:
                if item not in seen:
                    delta.append(item)
                    seen.add(item)
        except Exception:
            delta = []
    return delta, existing + delta


def _active_evidence(state: AutonomousAgentState) -> List[EvidenceRecord]:
    invalidated = set(state.get("invalidated_evidence_ids") or [])
    return [ev for ev in (state.get("evidence_pool") or []) if ev.get("evidence_id") not in invalidated]


def _get_mcp_client() -> McpClientAdapter:
    """获取绑定 EcommerceMcpServer 的标准 MCP 协议工具客户端。"""
    return McpClientAdapter(server=default_mcp_server)


def _build_tool_kwargs(tool_name: str, query_or_url: str) -> Dict[str, Any]:
    arg = (query_or_url or "").strip()
    if tool_name == "search_web":
        return {"query": arg, "limit": 15}
    if tool_name == "fetch_web_page":
        return {"url": arg, "max_chars": 4000}
    if tool_name == "search_1688_wholesale":
        return {"keyword": arg, "limit": 15}
    if tool_name == "search_amazon":
        return {"query": arg, "limit": 10}
    return {"query": arg}


def _default_fallback_tasks(keyword: str, scope: ResearchScope, steering: List[str]) -> List[SubagentTaskSpec]:
    """当主脑结构化决策解析失败且黑板尚无证据时的安全起步任务生成（受 Domain Scope 约束）。"""
    steer_suffix = f" {' '.join(steering[-2:])}".strip() if steering else ""
    base_query = f"{keyword} {steer_suffix}".strip()
    tasks = [
        SubagentTaskSpec(
            task_id="sub_web_init",
            role="全网口碑与市场情报专员",
            goal=f"检索并提炼【{keyword}】的全网主流定价、热销卖点与消费者高频差评痛点",
            allowed_tools=tuple(t for t in WEB_TOOLS if t in scope.allowed_tools),
            seed_queries=(base_query,),
            channel_hint="web",
        )
    ]
    if scope.needs_sourcing:
        if "search_1688_wholesale" in scope.allowed_tools:
            tasks.append(
                SubagentTaskSpec(
                    task_id="sub_1688_init",
                    role="1688源头工厂采购专员",
                    goal=f"核查【{keyword}】在1688源头工厂的一手出厂价梯度、MOQ起订量与核心产区",
                    allowed_tools=("search_1688_wholesale",),
                    seed_queries=(base_query, keyword),
                    channel_hint="sourcing",
                )
            )
        if "search_amazon" in scope.allowed_tools:
            tasks.append(
                SubagentTaskSpec(
                    task_id="sub_amazon_init",
                    role="海外亚马逊对标研究员",
                    goal=f"采集【{keyword}】在海外亚马逊的零售美元定价带、主力规格与竞品特征",
                    allowed_tools=("search_amazon",),
                    seed_queries=(base_query, keyword),
                    channel_hint="sourcing",
                )
            )
    return tasks


def _serialize_task(task: SubagentTaskSpec) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "role": task.role,
        "goal": task.goal,
        "allowed_tools": list(task.allowed_tools),
        "seed_queries": list(task.seed_queries),
        "channel_hint": task.channel_hint,
    }


def _deserialize_task(raw: Dict[str, Any]) -> SubagentTaskSpec:
    return SubagentTaskSpec(
        task_id=str(raw.get("task_id") or f"sub_{uuid.uuid4().hex[:6]}"),
        role=str(raw.get("role") or "市场调研专员"),
        goal=str(raw.get("goal") or "收集客观市场事实"),
        allowed_tools=tuple(str(x) for x in (raw.get("allowed_tools") or [])),
        seed_queries=tuple(str(x) for x in (raw.get("seed_queries") or [])),
        channel_hint=str(raw.get("channel_hint") or "general"),
    )


# ============================================================================
# 5. 核心多智能体节点实现（Lead Orchestrator + Parallel ReAct Subagents）
# ============================================================================

def lead_orchestrator_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """主协调代理（Lead Research Agent）：自主审查黑板证据、响应实时干预并动态组队派单。"""
    hooks = _get_hooks(state.get("hook_run_id", ""))
    turn = int(state.get("turn_count", 0)) + 1
    query = state.get("user_query") or state.get("keyword") or ""
    keyword = state.get("keyword") or extract_search_keyword(query) or query
    outline = list(state.get("outline") or [])

    cancelled = _is_cancelled(hooks, bool(state.get("cancelled", False)))
    steering_delta, all_inbox = _collect_new_steering(state, hooks)
    processed_count = int(state.get("processed_steering_count", 0))
    newly_arrived = all_inbox[processed_count:]

    if newly_arrived:
        _emit_progress(hooks, f"[steering] 主脑接收运行期动态干预指令并重新评估黑板: {newly_arrived[-1]}")

    scope = classify_research_scope(query, all_inbox)
    budget = ResearchBudget(
        max_turns=int(state.get("max_turns", 5)),
        max_parallel_subagents=int(state.get("max_parallel_subagents", 4)),
        max_subagent_steps=int(state.get("max_subagent_steps", 3)),
        timeout_seconds=float(state.get("timeout_seconds", 180.0)),
    )

    started_at = float(state.get("started_at") or time.perf_counter())
    elapsed_seconds = max(0.0, time.perf_counter() - started_at)
    active_ev = _active_evidence(state)

    hard_stop = evaluate_hard_stop(
        turn=turn,
        elapsed_seconds=elapsed_seconds,
        cancelled=cancelled,
        has_report=bool(state.get("report")),
        evidence_count=len(active_ev),
        budget=budget,
    )

    if hard_stop.should_stop:
        next_act = "synthesizer" if hard_stop.force_synthesize else "final_node"
        res: Dict[str, Any] = {
            "turn_count": turn,
            "outline": outline,
            "next_action": next_act,
            "pending_tasks": [],
            "guard_notices": [],
            "current_rationale": hard_stop.rationale,
            "system_status": hard_stop.system_status,
            "cancelled": cancelled,
            "stop_reason": hard_stop.stop_reason.value,
            "processed_steering_count": len(all_inbox),
        }
        if steering_delta:
            res["steering_inbox"] = steering_delta
        return res

    # 构建主脑 Observation 交由模型自主决策：继续动态派工，还是信息已足交卷合成
    ev_digest = [
        {
            "evidence_id": ev.get("evidence_id"),
            "role": ev.get("role"),
            "channel": ev.get("channel"),
            "status": ev.get("status"),
            "summary": ev.get("summary", "")[:500],
            "key_metrics": ev.get("key_metrics", [])[:8],
            "gaps_or_errors": ev.get("gaps_or_errors", [])[:5],
        }
        for ev in active_ev
    ]
    notices = list(state.get("guard_notices") or [])
    remaining_turns = max(0, budget.max_turns - turn)
    remaining_sec = max(0.0, budget.timeout_seconds - elapsed_seconds)

    steer_note = f"最新动态干预指令（必须优先响应）：{newly_arrived}\n" if newly_arrived else ""
    all_steer_note = f"累计干预指令：{all_inbox}\n" if all_inbox else ""
    notice_note = f"上轮领域守卫拦截提示：{notices}\n" if notices else ""
    mcp_specs = _get_mcp_client().list_tool_specs(scope.allowed_tools)

    prompt = (
        f"你是一名电商市场调研多智能体系统的主协调代理（Lead Research Agent）。\n"
        f"目标品类：【{keyword}】\n"
        f"用户完整诉求：{query}\n"
        f"{steer_note}{all_steer_note}{notice_note}"
        f"当前合法允许授权的 MCP 工具列表（tools/list 契约，严禁越界使用未列出工具）：\n{json.dumps(mcp_specs, ensure_ascii=False)}\n"
        f"剩余调度轮次：{remaining_turns}；剩余时间预算：{remaining_sec:.1f}s；单轮最多并发子智能体：{budget.max_parallel_subagents}。\n"
        f"当前黑板已有有效证据包（{len(ev_digest)} 份）：\n{json.dumps(ev_digest, ensure_ascii=False)}\n\n"
        "请自主判断当前决策：\n"
        "1. 若关键维度尚缺、存在口径矛盾或刚收到 Steering 纠偏需要重抓：输出 action='spawn_subagents'，"
        "合理拆解 1 到多个互不重叠的子任务并发执行（比如区分全网痛点、1688源头底价、Amazon海外竞品、交叉核验等），"
        "每个子任务明确 role、goal、allowed_tools（仅从白名单选）与 seed_queries；如旧证据被纠偏推翻，可在 invalidate_evidence_ids 中作废。\n"
        "2. 若已有证据已充分覆盖用户核心问题（且无高价值可补查缺口）：输出 action='finish' 并完善 3-4 条 outline 研报章节大纲。"
    )

    llm = LLMClient()
    parsed: Optional[_OrchestratorDecisionSchema] = None
    try:
        raw_decision = llm.chat_json(
            prompt=prompt,
            schema=_OrchestratorDecisionSchema,
            tier=ModelTier.FAST,
            system="你是客观严谨、擅长并行任务分解与质量核查的电商调研主协调智能体。严禁编造数据。",
            retries=2,
        )
        if isinstance(raw_decision, _OrchestratorDecisionSchema):
            parsed = raw_decision
        elif isinstance(raw_decision, dict) and raw_decision.get("action"):
            parsed = _OrchestratorDecisionSchema.model_validate(raw_decision)
    except Exception:
        parsed = None

    # 降级容错：若模型返回格式异常，首轮按允许范围安全派发起步探查，次轮且已有证据则直接收敛
    if parsed is None:
        if not active_ev:
            parsed = _OrchestratorDecisionSchema(
                action="spawn_subagents",
                thought="主脑结构化输出降级，按当前合法工具边界并发启动基础调研子智能体",
                outline=outline or ["市场需求与消费者痛点分析", "产品价格带与竞争格局", "供应链成本与选品建议"],
                subtasks=[
                    _ProposedSubtaskSchema(
                        role=t.role,
                        goal=t.goal,
                        allowed_tools=list(t.allowed_tools),
                        seed_queries=list(t.seed_queries),
                        channel_hint="sourcing" if t.channel_hint == "sourcing" else "web",
                    )
                    for t in _default_fallback_tasks(keyword, scope, all_inbox)
                ],
            )
        else:
            parsed = _OrchestratorDecisionSchema(
                action="finish",
                thought="主脑结构化输出降级，基于黑板既有有效证据收敛生成研报",
                outline=outline or ["市场需求与竞品格局", "多渠道数据汇总", "选品与操盘建议"],
            )

    if parsed.outline:
        outline = [str(x) for x in parsed.outline[:4]]
    elif not outline:
        outline = ["市场概况与消费者痛点", "竞品价格带与卖点格局", "选品优化与落地建议"]

    # 处理主脑声明作废的证据 + Steering 启发式辅助识别
    invalid_delta = [str(eid) for eid in parsed.invalidate_evidence_ids if eid]
    if newly_arrived and not invalid_delta and active_ev:
        for instr in newly_arrived:
            impact = classify_steering_impact(instr)
            for ev in active_ev:
                ch = ev.get("channel")
                if (impact.invalidate_web and ch == "web") or (impact.invalidate_wholesale and ch == "sourcing"):
                    if ev["evidence_id"] not in invalid_delta:
                        invalid_delta.append(ev["evidence_id"])

    # 若主脑判断 finish（且未因刚作废导致无剩余有效证据）
    remaining_valid_count = len([ev for ev in active_ev if ev.get("evidence_id") not in set(invalid_delta)])
    if parsed.action == "finish" and remaining_valid_count > 0:
        _emit_progress(hooks, f"[agent_handoff] role=main_agent 确认黑板 {remaining_valid_count} 份证据充分，交接研报合成专家", 80)
        res = {
            "turn_count": turn,
            "outline": outline,
            "next_action": "synthesizer",
            "pending_tasks": [],
            "guard_notices": [],
            "current_rationale": parsed.thought or "主脑确认各维度证据充实，开始合成终审研报",
            "system_status": "REPORTING",
            "cancelled": False,
            "stop_reason": StopReason.CONTINUE.value,
            "processed_steering_count": len(all_inbox),
        }
        if invalid_delta:
            res["invalidated_evidence_ids"] = invalid_delta
        if steering_delta:
            res["steering_inbox"] = steering_delta
        return res

    # 将主脑规划的子任务送入 Domain 硬守卫裁剪越权工具与并发数
    proposed_specs = [
        SubagentTaskSpec(
            task_id=f"t{turn}_sub{idx + 1}_{uuid.uuid4().hex[:4]}",
            role=st.role,
            goal=st.goal,
            allowed_tools=tuple(st.allowed_tools),
            seed_queries=tuple(st.seed_queries or [keyword]),
            channel_hint=st.channel_hint,
        )
        for idx, st in enumerate(parsed.subtasks)
    ]

    if not proposed_specs and not remaining_valid_count:
        proposed_specs = _default_fallback_tasks(keyword, scope, all_inbox)

    auth_plan = authorize_subagent_tasks(proposed_specs, scope=scope, budget=budget)
    authorized_tasks = list(auth_plan.tasks)
    new_notices = list(auth_plan.rejected_reasons)

    # 若主脑所有提案均因越权被驳回且黑板尚未有有效证据，注入合法兜底调研任务避免空转
    if not authorized_tasks and remaining_valid_count == 0:
        fallback_plan = authorize_subagent_tasks(_default_fallback_tasks(keyword, scope, all_inbox), scope=scope, budget=budget)
        authorized_tasks = list(fallback_plan.tasks)
        new_notices.append("主脑原提案均超出当前调研边界，已按允许白名单重置起步任务")

    if not authorized_tasks:
        _emit_progress(hooks, "[agent_handoff] main_agent 暂无更多合法增量派工，收敛现有证据撰写研报", 80)
        next_act = "synthesizer"
        sys_status = "REPORTING"
    else:
        roles_str = " / ".join(t.role for t in authorized_tasks)
        _bump_progress(hooks, 10, 25, f"[agent_start] main_agent 第 {turn} 轮并行派发 {len(authorized_tasks)} 位子智能体 ({roles_str})")
        next_act = "dispatch_subagents"
        sys_status = "STEERING_REDIRECTED" if newly_arrived else "COLLECTING"

    res = {
        "turn_count": turn,
        "outline": outline,
        "next_action": next_act,
        "pending_tasks": [_serialize_task(t) for t in authorized_tasks],
        "guard_notices": new_notices,
        "current_rationale": parsed.thought or "主脑派发并行子智能体探索市场事实",
        "system_status": sys_status,
        "cancelled": False,
        "stop_reason": StopReason.CONTINUE.value,
        "processed_steering_count": len(all_inbox),
    }
    if invalid_delta:
        res["invalidated_evidence_ids"] = invalid_delta
    if steering_delta:
        res["steering_inbox"] = steering_delta
    return res


def subagent_worker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """受派子智能体执行节点：运行独立有界 ReAct 工具探索循环（Think -> Tool Gate -> Observe）。"""
    t0 = time.perf_counter()
    hooks = _get_hooks(str(state.get("hook_run_id") or ""))
    task = _deserialize_task(state.get("current_task") or {})
    user_query = str(state.get("user_query") or state.get("keyword") or "")
    keyword = str(state.get("keyword") or user_query)
    steering = list(state.get("steering_inbox") or [])
    started_at = float(state.get("started_at") or time.perf_counter())
    timeout_seconds = float(state.get("timeout_seconds", 180.0))
    max_steps = max(1, int(state.get("max_subagent_steps", 3)))
    rationale = str(state.get("current_rationale") or task.goal)

    scope = classify_research_scope(user_query, steering)
    ctx = SubagentContext(
        conversation_id=f"{task.task_id}_{int(time.time())}",
        role=task.role,
    )

    _emit_progress(hooks, f"[agent_handoff] 角色交接: lead_agent ➔ {task.role} · 目标: '{task.goal}'")

    tools_records: List[AuditableToolRecord] = []
    scratch_files: List[str] = []
    platform_items: Dict[str, List[Dict[str, Any]]] = {"web": [], "p1688": [], "amazon": []}
    tool_counts: Dict[str, int] = {}
    react_observations: List[Dict[str, Any]] = []
    final_summary = ""
    key_metrics: List[str] = []
    gaps_or_errors: List[str] = []

    llm = LLMClient()
    mcp_client = _get_mcp_client()
    mcp_specs = mcp_client.list_tool_specs(task.allowed_tools)

    def _run_guarded_tool(tool_name: str, query_or_url: str, step_idx: int) -> ToolOutcome:
        # 1. Domain 双重硬守卫（业务边界 + 当前任务授权）
        try:
            enforce_tool_allowed(tool_name, scope, task.allowed_tools)
        except Denied as exc:
            return ToolOutcome(
                tool_name=tool_name,
                status="error",
                error_reason=exc.message,
            )

        if _is_cancelled(hooks):
            return ToolOutcome(
                tool_name=tool_name,
                status="cancelled",
                error_reason="收到任务取消信号，中止工具调用",
            )

        remaining = timeout_seconds - (time.perf_counter() - started_at) if timeout_seconds > 0 else None
        kwargs = _build_tool_kwargs(tool_name, query_or_url)

        _emit_progress(
            hooks,
            f"[tool_execution_start] [{task.role}] MCP tools/call: {tool_name} · 入参: {json.dumps(kwargs, ensure_ascii=False)}",
        )

        outcome = mcp_client.call_tool(
            tool_name=tool_name,
            arguments=kwargs,
            remaining_seconds=remaining,
            cancel_event=hooks.cancel_event,
        )

        # 原始抓取写入隔离 Scratch 沙箱，不污染状态黑板
        if outcome.items:
            scratch_name = f"step{step_idx}_{tool_name}.json"
            s_path = ctx.write_scratch(scratch_name, json.dumps(outcome.items, ensure_ascii=False, indent=2))
            scratch_files.append(str(s_path))

        if tool_name == "search_web":
            platform_items["web"].extend(outcome.items)
        elif tool_name == "search_1688_wholesale":
            platform_items["p1688"].extend(outcome.items)
        elif tool_name == "search_amazon":
            platform_items["amazon"].extend(outcome.items)

        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + outcome.data_count

        summary_txt = (
            f"状态 {outcome.status} · 返回 {outcome.data_count} 条结果"
            if outcome.status in ("ok", "empty")
            else f"状态 {outcome.status} · {outcome.error_reason}"
        )
        tools_records.append(
            {
                "tool_name": tool_name,
                "parameters": kwargs,
                "status": outcome.status,
                "result_summary": summary_txt,
                "data_count": outcome.data_count,
                "elapsed": outcome.elapsed,
            }
        )

        _bump_progress(
            hooks,
            8,
            75,
            f"[tool_execution_end] [{task.role}] 工具 {tool_name} 完毕 · {summary_txt}",
        )
        return outcome

    # ── 子智能体有界 ReAct 探索循环 ──
    for step_idx in range(1, max_steps + 1):
        if _is_cancelled(hooks):
            gaps_or_errors.append("任务中途取消")
            break
        if timeout_seconds > 0 and (time.perf_counter() - started_at) >= timeout_seconds:
            gaps_or_errors.append("达到全局时间预算上限")
            break

        step_prompt = (
            f"你是电商调研子智能体：【{task.role}】。\n"
            f"核心目标：{task.goal}\n"
            f"品类词：【{keyword}】；用户原诉求：{user_query}\n"
            f"授权可用 MCP 工具（tools/list 契约，严禁越界）：\n{json.dumps(mcp_specs, ensure_ascii=False)}\n"
            f"建议起步词：{list(task.seed_queries)}\n"
            f"当前进度：第 {step_idx}/{max_steps} 步。\n"
            f"历史观测记录：\n{json.dumps(react_observations, ensure_ascii=False)}\n\n"
            "请自主决策下一步：\n"
            "- 若尚需收集信息，或上一步 status='empty'（应换同义词/更宽泛核心词再查），或想用 fetch_web_page 深抓上一步返回的某个 url 详情：输出 action='call_tool'，填写合法 tool_name 和 query_or_url。\n"
            "- 若已获取足够实证，或所有渠道均 status='error'/'timeout' 无法继续抓取：输出 action='finish'，并严谨填写 findings_summary、key_metrics 与 gaps_or_errors。\n"
            "【铁律】：严禁编造工具未返回的任何数字或链接。"
        )

        step_dec: Optional[_SubagentStepSchema] = None
        try:
            raw_step = llm.chat_json(
                prompt=step_prompt,
                schema=_SubagentStepSchema,
                tier=ModelTier.FAST,
                system=f"你是专注【{task.role}】的客观市场调查专员，基于工具实测观测执行 ReAct 循环。",
                retries=2,
            )
            if isinstance(raw_step, _SubagentStepSchema):
                step_dec = raw_step
            elif isinstance(raw_step, dict) and raw_step.get("action"):
                step_dec = _SubagentStepSchema.model_validate(raw_step)
        except Exception:
            step_dec = None

        # 解析失败降级兜底：首步用默认工具与起步词实际跑一次，已有观测则直接收敛
        if step_dec is None:
            if not tools_records and task.allowed_tools:
                default_tool = task.allowed_tools[0]
                default_query = task.seed_queries[0] if task.seed_queries else keyword
                step_dec = _SubagentStepSchema(
                    action="call_tool",
                    thought="执行起步检索收集原始情报",
                    tool_name=default_tool,
                    query_or_url=default_query,
                )
            else:
                step_dec = _SubagentStepSchema(
                    action="finish",
                    thought="已有工具实测观测，收敛当前结果",
                    findings_summary="",
                )

        if step_dec.action == "finish":
            final_summary = (step_dec.findings_summary or "").strip()
            key_metrics = [str(m) for m in step_dec.key_metrics if m]
            gaps_or_errors.extend(str(g) for g in step_dec.gaps_or_errors if g)
            break

        tool_name = (step_dec.tool_name or (task.allowed_tools[0] if task.allowed_tools else "")).strip()
        q_arg = (step_dec.query_or_url or (task.seed_queries[0] if task.seed_queries else keyword)).strip()
        outcome = _run_guarded_tool(tool_name, q_arg, step_idx)

        react_observations.append(
            {
                "step": step_idx,
                "tool": tool_name,
                "arg": q_arg,
                "status": outcome.status,
                "data_count": outcome.data_count,
                "error_reason": outcome.error_reason,
                "preview": outcome.text_preview[:1500],
            }
        )
        if outcome.status in ("error", "timeout", "cancelled") and outcome.error_reason:
            gaps_or_errors.append(f"{tool_name}({outcome.status}): {outcome.error_reason}")

    # 若步数耗尽仍未调用 finish，基于真实观测做一次客观提炼
    if not final_summary:
        if react_observations:
            try:
                synth_prompt = (
                    f"你是【{task.role}】，请仅依据以下工具真实返回观测（严禁虚构），针对目标『{task.goal}』归纳客观事实要点：\n"
                    f"{json.dumps(react_observations, ensure_ascii=False)}"
                )
                final_summary = llm.chat(
                    synth_prompt,
                    tier=ModelTier.FAST,
                    system="你是严禁幻觉与造假的市场分析专员。",
                    max_tokens=800,
                ).strip()
            except Exception:
                final_summary = ""
        if not final_summary:
            total_items = sum(t["data_count"] for t in tools_records)
            final_summary = (
                f"[{task.role}] 完成 {len(tools_records)} 次工具调用，累计获取 {total_items} 条原始数据"
                + (f"；存在异常/缺口：{'；'.join(gaps_or_errors)}" if gaps_or_errors else "。")
            )

    elapsed = round(time.perf_counter() - t0, 2)
    channel = "sourcing" if any(t["tool_name"] in SOURCING_TOOLS for t in tools_records) or task.channel_hint == "sourcing" else "web"
    has_ok = any(t["status"] == "ok" and t["data_count"] > 0 for t in tools_records)
    has_err = any(t["status"] in ("error", "timeout") for t in tools_records)
    ev_status = "ok" if has_ok else ("error" if has_err else "empty")

    _emit_progress(hooks, f"[agent_end] 【{task.role}】完成探索 · 状态: {ev_status} (耗时 {elapsed}s)")

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": task.role,
        "type_name": task.task_id,
        "state_from": "SPAWNED",
        "state_to": "COMPLETED" if ev_status != "error" else "PARTIAL_OR_FAILED",
        "decision_rationale": rationale,
        "tools": tools_records,
        "elapsed": elapsed,
        "scratch_files": scratch_files,
    }

    evidence: EvidenceRecord = {
        "evidence_id": task.task_id,
        "subagent_id": ctx.conversation_id,
        "role": task.role,
        "channel": channel,
        "goal": task.goal,
        "summary": final_summary,
        "key_metrics": key_metrics,
        "gaps_or_errors": gaps_or_errors,
        "platform_items": platform_items,
        "tool_counts": tool_counts,
        "status": ev_status,
        "elapsed": elapsed,
    }

    return {
        "evidence_pool": [evidence],
        "audit_trail": [transition],
    }


def synthesizer_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终审研报合成专家（ModelTier.STRONG）：仅引用黑板实证生成带缺口声明的 Markdown 研报。"""
    t0 = time.perf_counter()
    hooks = _get_hooks(state.get("hook_run_id", ""))
    keyword = state.get("keyword") or state.get("user_query") or ""
    user_query = state.get("user_query") or keyword
    outline = state.get("outline") or ["市场概况与用户痛点", "竞品价格带格局", "落地决策建议"]
    inbox = state.get("steering_inbox") or []
    active_ev = _active_evidence(state)

    _emit_progress(hooks, "[agent_handoff] 角色交接: lead_agent ➔ 终审研报合成专家 · 整合黑板有效证据", 85)

    ctx = SubagentContext(
        conversation_id=f"rep_{state.get('job_id', 'task')}_{int(time.time())}",
        role="终审研报合成专家",
    )

    evidence_payload = [
        {
            "evidence_id": ev.get("evidence_id"),
            "role": ev.get("role"),
            "channel": ev.get("channel"),
            "status": ev.get("status"),
            "goal": ev.get("goal"),
            "summary": ev.get("summary"),
            "key_metrics": ev.get("key_metrics"),
            "gaps_or_errors": ev.get("gaps_or_errors"),
            "tool_counts": ev.get("tool_counts"),
        }
        for ev in active_ev
    ]

    steer_block = f"\n【运行期动态干预要求（必须显式回应落地）】：{'；'.join(inbox)}\n" if inbox else ""
    prompt = (
        f"目标品类：【{keyword}】\n"
        f"用户完整诉求：{user_query}\n"
        f"规划大纲章节：{'；'.join(outline)}\n"
        f"{steer_block}\n"
        f"【黑板已回收有效证据包（共 {len(evidence_payload)} 份）】：\n"
        f"{json.dumps(evidence_payload, ensure_ascii=False)}\n\n"
        "请综合上述多智能体真实采回的证据撰写结构化 Markdown 市场调研报告。\n"
        "【严格准则】：\n"
        "1. 一切价格、起订量、销量、差评与工厂/商品线索必须严格来自上述黑板证据，绝不允许编造未出现的数字；\n"
        "2. 若某渠道 status 为 empty/error 或 gaps_or_errors 非空，必须在报告对应章节客观说明数据缺失原因与可替代方案，严禁粉饰或凭空补齐；\n"
        "3. 结合用户诉求给出可落地的差异化与风险提示。"
    )

    llm = LLMClient()
    _emit_progress(hooks, "[tool_execution_start] [终审研报合成专家] 调度工具: generate_markdown_report", 90)
    try:
        final_report = llm.chat(
            prompt,
            tier=ModelTier.STRONG,
            system="你是资深电商操盘负责人。输出客观、可核验的高质量 Markdown 研报，绝对禁止虚构数据。",
        )
    except Exception as exc:
        sections = [
            f"### {ev.get('role')}（{ev.get('status')}）\n{ev.get('summary', '')}"
            for ev in active_ev
        ]
        body = "\n\n".join(sections) if sections else "本次暂无可用实测证据。"
        final_report = (
            f"# 【{keyword}】电商市场调研报告（降级结构化汇总）\n\n"
            f"> 注：终审大模型生成阶段发生异常（{type(exc).__name__}），以下内容严格由各子智能体实测证据直接汇总，未作二次推演。\n\n"
            f"{body}"
        )

    art_meta = ArtifactMetadata(summary=f"【{keyword}】电商多智能体调研报告", user_facing=True)
    art_file = ctx.write_artifact("market_research_report.md", final_report, metadata=art_meta)
    elapsed = round(time.perf_counter() - t0, 2)

    _emit_progress(
        hooks,
        f"[tool_execution_end] [终审研报合成专家] 研报生成完毕 · {len(final_report)} 字符 (耗时 {elapsed}s)",
        95,
    )

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "终审研报合成专家",
        "type_name": "synthesizer_agent",
        "state_from": "SYNTHESIZING",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": [
            {
                "tool_name": "generate_markdown_report",
                "parameters": {"evidence_count": len(active_ev), "outline_count": len(outline)},
                "status": "ok",
                "result_summary": f"基于 {len(active_ev)} 份黑板实证生成终审 Markdown 研报（{len(final_report)} 字符）。",
                "data_count": len(active_ev),
                "elapsed": elapsed,
            }
        ],
        "elapsed": elapsed,
        "scratch_files": [],
    }

    return {
        "report": final_report,
        "artifacts": [str(art_file)],
        "audit_trail": [transition],
        "system_status": "COMPLETED" if state.get("stop_reason") == StopReason.CONTINUE.value else state.get("system_status", "COMPLETED"),
    }


def final_persistence_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终态沉淀节点：仅在非取消状态下将大纲与实测摘要写入租户长效知识库。"""
    cancelled = bool(state.get("cancelled", False))
    if cancelled:
        return {"system_status": "CANCELLED"}

    keyword = state.get("keyword") or ""
    tenant_id = state.get("tenant_id") or "default"
    report = state.get("report") or ""
    outline = state.get("outline") or []

    try:
        mem = get_memory_store(tenant_id=tenant_id)
        if keyword and outline:
            mem.remember_fact(subject=keyword, predicate="全景调研大纲", object_val="；".join(outline), confidence=1.0)
        if keyword and report:
            mem.remember_fact(subject=keyword, predicate="全景调研报告摘要", object_val=report[:250], confidence=0.95)
    except Exception:
        pass

    final_status = "TIMEOUT" if state.get("stop_reason") == StopReason.TIMEOUT.value and not report else "COMPLETED"
    return {"system_status": final_status}


# ============================================================================
# 6. LangGraph 动态并行路由与状态图构建
# ============================================================================

def orchestrator_router(state: AutonomousAgentState) -> Any:
    """动态扇出路由器：返回并行的 Send(subagent_worker) 列表，或跳转到终审/终止节点。"""
    next_act = state.get("next_action", "final_node")
    if next_act == "dispatch_subagents":
        tasks = state.get("pending_tasks") or []
        if tasks:
            return [
                Send(
                    "subagent_worker",
                    {
                        "current_task": task,
                        "hook_run_id": state.get("hook_run_id", ""),
                        "user_query": state.get("user_query", ""),
                        "keyword": state.get("keyword", ""),
                        "steering_inbox": list(state.get("steering_inbox") or []),
                        "started_at": state.get("started_at", 0.0),
                        "timeout_seconds": state.get("timeout_seconds", 180.0),
                        "max_subagent_steps": state.get("max_subagent_steps", 3),
                        "current_rationale": state.get("current_rationale", ""),
                    },
                )
                for task in tasks
            ]
        return "synthesizer"
    if next_act == "synthesizer":
        return "synthesizer"
    return "final_node"


def build_autonomous_agent_graph(checkpointer: Optional[Any] = None) -> Any:
    """构建动态并行 Orchestrator–Workers 多智能体 LangGraph 状态图。"""
    builder = StateGraph(AutonomousAgentState)

    builder.add_node("orchestrator", lead_orchestrator_node)
    builder.add_node("subagent_worker", subagent_worker_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("final_node", final_persistence_node)

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges(
        "orchestrator",
        orchestrator_router,
        ["subagent_worker", "synthesizer", "final_node"],
    )
    builder.add_edge("subagent_worker", "orchestrator")
    builder.add_edge("synthesizer", "final_node")
    builder.add_edge("final_node", END)

    return builder.compile(checkpointer=checkpointer)


# ============================================================================
# 7. 统一流式执行入口（供 Worker 进程与评测沙箱调用）
# ============================================================================

def execute_autonomous_agents(
    keyword: str,
    user_query: str = "",
    job_id: str = "",
    tenant_id: str = "default",
    run_version: int = 1,
    conversation_id: str = "",
    checkpointer: Optional[Any] = None,
    emit_event_fn: Optional[Callable[[str, int], None]] = None,
    initial_steering: Optional[List[str]] = None,
    timeout_seconds: float = 180.0,
    max_turns: int = 5,
    max_parallel_subagents: int = 4,
    max_subagent_steps: int = 3,
    check_cancel_fn: Optional[Callable[[], bool]] = None,
    fetch_steering_fn: Optional[Callable[[], List[str]]] = None,
) -> Dict[str, Any]:
    """执行动态并行多智能体调研系统并实时推送各层可审计事件。"""
    started_at = time.perf_counter()
    hook_run_id = f"run_{uuid.uuid4().hex}"
    hooks = _RuntimeHooks(
        check_cancel_fn=check_cancel_fn,
        fetch_steering_fn=fetch_steering_fn,
        emit_fn=emit_event_fn,
    )

    with _HOOKS_LOCK:
        _HOOKS_BY_RUN_ID[hook_run_id] = hooks

    init_state: AutonomousAgentState = {
        "job_id": job_id or f"job_{int(time.time())}",
        "tenant_id": tenant_id,
        "run_version": run_version,
        "conversation_id": conversation_id or f"conv_{int(time.time())}",
        "hook_run_id": hook_run_id,
        "user_query": user_query or keyword,
        "keyword": keyword,
        "steering_inbox": list(initial_steering or []),
        "processed_steering_count": 0,
        "system_status": "INIT",
        "next_action": "orchestrator",
        "current_rationale": "激活 Orchestrator-Workers 自治多智能体调研系统",
        "turn_count": 0,
        "max_turns": max_turns,
        "max_parallel_subagents": max_parallel_subagents,
        "max_subagent_steps": max_subagent_steps,
        "started_at": started_at,
        "timeout_seconds": timeout_seconds,
        "cancelled": False,
        "stop_reason": StopReason.CONTINUE.value,
        "outline": [],
        "pending_tasks": [],
        "guard_notices": [],
        "evidence_pool": [],
        "invalidated_evidence_ids": [],
        "audit_trail": [],
        "artifacts": [],
        "report": "",
    }

    graph = build_autonomous_agent_graph(checkpointer=checkpointer)
    thread_id = f"research_{job_id}_{run_version}" if job_id else f"research_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    _emit_progress(hooks, f"[agent_start] 激活自治多智能体主协调代理 (main_agent) · 目标品类: 【{keyword}】", 5)

    merged_state: Dict[str, Any] = dict(init_state)
    for list_key in ("steering_inbox", "evidence_pool", "invalidated_evidence_ids", "audit_trail", "artifacts"):
        merged_state[list_key] = list(init_state[list_key])

    try:
        for chunk in graph.stream(init_state, config=config, stream_mode="updates"):
            for _, delta in chunk.items():
                if not isinstance(delta, dict):
                    continue
                for k, v in delta.items():
                    if k in (
                        "steering_inbox",
                        "evidence_pool",
                        "invalidated_evidence_ids",
                        "audit_trail",
                        "artifacts",
                    ) and isinstance(v, list):
                        merged_state[k].extend(v)
                    else:
                        merged_state[k] = v
    finally:
        with _HOOKS_LOCK:
            _HOOKS_BY_RUN_ID.pop(hook_run_id, None)

    total_elapsed = round(time.perf_counter() - started_at, 1)

    subagents_trace: List[Dict[str, Any]] = []
    for tr in merged_state.get("audit_trail", []):
        tool_records = tr.get("tools", [])
        subagents_trace.append(
            {
                "id": tr["agent_id"],
                "role": tr["role"],
                "type": tr["type_name"],
                "type_name": tr["type_name"],
                "state": tr["state_to"].lower(),
                "status": tr["state_to"].lower(),
                "decision_rationale": tr["decision_rationale"],
                "elapsed": tr["elapsed"],
                "tools": [
                    {
                        "name": t["tool_name"],
                        "description": t["result_summary"],
                        "status": t.get("status", "ok"),
                        "data_count": t["data_count"],
                        "parameters": t["parameters"],
                    }
                    for t in tool_records
                ],
                "scratch_files": tr["scratch_files"],
                "brief": tool_records[0]["result_summary"] if tool_records else tr["decision_rationale"],
            }
        )

    # 聚合有效黑板证据中的真实平台抓包条目（绝不虚构）
    invalidated = set(merged_state.get("invalidated_evidence_ids") or [])
    active_ev = [ev for ev in (merged_state.get("evidence_pool") or []) if ev.get("evidence_id") not in invalidated]
    web_items: List[Dict[str, Any]] = []
    p1688_items: List[Dict[str, Any]] = []
    amz_items: List[Dict[str, Any]] = []
    web_briefs: List[str] = []
    sourcing_briefs: List[str] = []

    for ev in active_ev:
        p_items = ev.get("platform_items") or {}
        web_items.extend(p_items.get("web") or [])
        p1688_items.extend(p_items.get("p1688") or [])
        amz_items.extend(p_items.get("amazon") or [])
        if ev.get("channel") == "web" and ev.get("summary"):
            web_briefs.append(str(ev["summary"]))
        elif ev.get("channel") == "sourcing" and ev.get("summary"):
            sourcing_briefs.append(str(ev["summary"]))

    platforms_summary = []
    if web_items or web_briefs:
        platforms_summary.append(
            {
                "name": "web",
                "label": "全网网页实时搜索",
                "state": "ok" if web_items else "empty",
                "state_label": "已完成" if web_items else "无匹配条目",
                "items_count": len(web_items),
                "items": web_items[:20],
                "brief": ("；".join(web_briefs))[:180],
            }
        )
    if p1688_items or amz_items or sourcing_briefs:
        sourcing_count = len(p1688_items) + len(amz_items)
        platforms_summary.append(
            {
                "name": "p1688",
                "label": "1688与亚马逊货源比价",
                "state": "ok" if sourcing_count > 0 else "empty",
                "state_label": "已完成" if sourcing_count > 0 else "无匹配条目",
                "items_count": sourcing_count,
                "items": (p1688_items + amz_items)[:20],
                "brief": ("；".join(sourcing_briefs))[:180],
            }
        )

    if merged_state.get("cancelled"):
        _emit_progress(hooks, f"[system] 任务已按用户取消请求提前终止 (耗时 {total_elapsed}s)", 100)
    else:
        _emit_progress(hooks, f"[agent_end] 多智能体自主协同达成目标 · 终审研报交付就绪 (总耗时 {total_elapsed}s)", 100)

    return {
        "report": merged_state.get("report", ""),
        "reply": merged_state.get("report", ""),
        "outline": merged_state.get("outline", []),
        "platforms": platforms_summary,
        "model_calls": len(merged_state.get("audit_trail", [])) + int(merged_state.get("turn_count", 0)),
        "elapsed": total_elapsed,
        "artifacts": merged_state.get("artifacts", []),
        "subagents": subagents_trace,
        "audit_trail": merged_state.get("audit_trail", []),
        "evidence_pool": active_ev,
        "conversation_id": conversation_id,
        "keyword": keyword,
        "system_status": merged_state.get("system_status", "COMPLETED"),
        "stop_reason": merged_state.get("stop_reason", StopReason.COMPLETED.value),
        "cancelled": bool(merged_state.get("cancelled", False)),
    }
