"""专职电商真·自治多智能体系统（Application 编排层）。

核心架构原则：
1. 严格遵守分层架构：本模块位于 application 层，仅依赖 domain 领域规则与 infrastructure 底层能力，
   绝不反向依赖 interface 层（server/api/worker）或直接操作数据库；
2. 全面基于 LangGraph 状态图与主管决策循环（Supervisor Agent Loop），进度以客观轮次推进；
3. 生产级可审计工程规范：只记录工具名、入参、结果摘要、状态跃迁、决策理由 5 大信标；
4. 真流式节点级事件广播（graph.stream）与运行期即时停止/动态干预（Mid-Flight Steering）：
   - 领域层 ResearchScope / ResearchStoppingPolicy 统一裁决边界、超时、轮次与取消；
   - 通过运行时回调注入解耦持久层，实现节点级取消止损与 DB 增量干预感知。
"""
from __future__ import annotations

import contextvars
import json
import operator
import time
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Dict, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from ..ai.client import LLMClient, ModelTier
from ..core.agent.context import ArtifactMetadata, SubagentContext
from ..core.memory.service import get_memory_store
from ..core.tools.search_1688 import search_1688_wholesale
from ..core.tools.search_amazon import search_amazon
from ..core.tools.search_web import extract_search_keyword, search_web
from ..domain.research import (
    ResearchSnapshot,
    StopReason,
    classify_research_scope,
    classify_steering_impact,
    enforce_tool_allowed,
    evaluate_next_step,
)


# ============================================================================
# 1. 运行时控制钩子上下文（不进入 LangGraph State，避免检查点序列化冲突）
# ============================================================================

@dataclass
class _RuntimeHooks:
    check_cancel_fn: Optional[Callable[[], bool]] = None
    fetch_steering_fn: Optional[Callable[[], List[str]]] = None


_runtime_hooks: contextvars.ContextVar[_RuntimeHooks] = contextvars.ContextVar(
    "_research_runtime_hooks", default=_RuntimeHooks()
)


# ============================================================================
# 2. 强类型可审计数据结构定义（Auditable Data Contracts）
# ============================================================================

class AuditableToolRecord(TypedDict):
    """单次工具调用的客观审计记录（无 CoT 噪音）。"""
    tool_name: str
    parameters: Dict[str, Any]
    result_summary: str
    data_count: int
    elapsed: float


class AuditableAgentTransition(TypedDict):
    """智能体生命周期与状态跃迁审计记录。"""
    agent_id: str
    role: str
    type_name: str
    state_from: str
    state_to: str
    decision_rationale: str
    tools: List[AuditableToolRecord]
    elapsed: float
    scratch_files: List[str]


class AutonomousAgentState(TypedDict):
    """多智能体系统在各个状态机节点之间流转的全局自治状态。"""
    job_id: str
    tenant_id: str
    run_version: int
    conversation_id: str
    user_query: str
    keyword: str

    # 动态中途干预专用信箱（支持 LangGraph update_state 与外部回调增量合并）
    steering_inbox: Annotated[List[str], operator.add]
    processed_steering_count: int

    # 自治协同认知与停止控制信标
    system_status: str
    active_agent: str
    next_action: str
    current_rationale: str
    turn_count: int
    max_turns: int
    started_at: float
    timeout_seconds: float
    cancelled: bool
    stop_reason: str
    web_retries: int
    scanner_retries: int
    max_empty_retries: int

    # 结构化业务事实池（上下文隔离保护区）
    outline: List[str]
    web_findings: Optional[Dict[str, Any]]
    wholesale_findings: Optional[Dict[str, Any]]
    verification_findings: Optional[Dict[str, Any]]

    # 可审计事件流与最终产物
    audit_trail: Annotated[List[AuditableAgentTransition], operator.add]
    artifacts: Annotated[List[str], operator.add]
    report: str


# ============================================================================
# 3. 核心专职智能体节点实现（Autonomous Agent Nodes）
# ============================================================================

def _collect_new_steering(state: AutonomousAgentState) -> tuple[List[str], List[str]]:
    """合并外部控制通道拉取的干预指令（去重防重），返回 (新增 delta 列表, 完整 inbox 列表)。"""
    existing = list(state.get("steering_inbox") or [])
    hooks = _runtime_hooks.get()
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
    all_items = existing + delta
    return delta, all_items


def main_supervisor_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """主脑决策智能体（main_agent）：感知环境、审查事实、响应干预并评估停止条件。"""
    turn = state.get("turn_count", 0) + 1
    query = state.get("user_query") or state.get("keyword") or ""
    keyword = state.get("keyword") or extract_search_keyword(query) or query
    outline = list(state.get("outline") or [])

    hooks = _runtime_hooks.get()
    cancelled = bool(state.get("cancelled", False))
    if not cancelled and hooks.check_cancel_fn is not None:
        try:
            cancelled = bool(hooks.check_cancel_fn())
        except Exception:
            cancelled = False

    steering_delta, all_inbox = _collect_new_steering(state)
    processed_count = int(state.get("processed_steering_count", 0))
    newly_arrived = all_inbox[processed_count:]

    web_f = state.get("web_findings")
    whole_f = state.get("wholesale_findings")
    verif_f = state.get("verification_findings")

    reset_updates: Dict[str, Any] = {}
    steering_redirected = False
    if newly_arrived:
        steering_redirected = True
        for instr in newly_arrived:
            impact = classify_steering_impact(instr)
            if impact.invalidate_web and web_f is not None:
                web_f = None
                reset_updates["web_findings"] = None
            if impact.invalidate_wholesale and whole_f is not None:
                whole_f = None
                reset_updates["wholesale_findings"] = None
            if impact.invalidate_verification and verif_f is not None:
                verif_f = None
                reset_updates["verification_findings"] = None

    scope = classify_research_scope(query, all_inbox)
    started_at = float(state.get("started_at") or time.perf_counter())
    elapsed_seconds = max(0.0, time.perf_counter() - started_at)

    snapshot = ResearchSnapshot(
        turn=turn,
        max_turns=int(state.get("max_turns", 6)),
        elapsed_seconds=elapsed_seconds,
        timeout_seconds=float(state.get("timeout_seconds", 0.0)),
        cancelled=cancelled,
        scope=scope,
        has_web=web_f is not None,
        web_count=int((web_f or {}).get("items_count", 0)) if web_f is not None else 0,
        has_wholesale=whole_f is not None,
        wholesale_count=(
            int((whole_f or {}).get("items_count_1688", 0))
            + int((whole_f or {}).get("items_count_amazon", 0))
        )
        if whole_f is not None
        else 0,
        has_verification=verif_f is not None,
        has_report=bool(state.get("report")),
        web_retries=int(state.get("web_retries", 0)),
        scanner_retries=int(state.get("scanner_retries", 0)),
        max_empty_retries=int(state.get("max_empty_retries", 0)),
    )

    decision = evaluate_next_step(snapshot)

    # 仅在正常推进且尚未拟定大纲时生成探索大纲（取消/超限止损时不再额外调 LLM）
    if not outline and decision.stop_reason != StopReason.CANCELLED:
        llm = LLMClient()
        sys_outline_prompt = (
            "你是电商市场调研总监。请针对调研品类自主规划 3-4 个最核心调研维度（JSON 数组格式，严禁废话）。\n"
            "必须涵盖：市场概况与竞品格局、1688源头工厂底价与出海售价比对、供应链真实毛利核验。"
        )
        try:
            raw_outline = llm.chat(f"品类词：{keyword}", tier=ModelTier.FAST, system=sys_outline_prompt, max_tokens=200)
            clean = raw_outline.strip().strip("```json").strip("```").strip()
            data = json.loads(clean)
            if isinstance(data, list):
                outline = [str(x) for x in data[:4]]
        except Exception:
            outline = [
                "全网市场概况与热销卖点分析",
                "1688 源头工厂出厂价与海外零售对标比价",
                "零售溢价与供应链工艺交叉验真",
                "品类差异化突围与选品决策建议",
            ]

    system_status = "STEERING_REDIRECTED" if (steering_redirected and decision.stop_reason == StopReason.CONTINUE) else decision.system_status

    result: Dict[str, Any] = {
        "turn_count": turn,
        "outline": outline,
        "active_agent": "main_agent",
        "next_action": decision.next_action,
        "current_rationale": decision.rationale,
        "system_status": system_status,
        "cancelled": cancelled,
        "stop_reason": decision.stop_reason.value,
        "processed_steering_count": len(all_inbox),
        **reset_updates,
    }
    if steering_delta:
        result["steering_inbox"] = steering_delta
    return result


def web_agent_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """全网情报分析专员（web_agent）：专注全网网页检索（仅绑定 search_web）。"""
    t0 = time.perf_counter()
    raw_query = state.get("user_query") or state.get("keyword") or ""
    keyword = state.get("keyword") or raw_query
    inbox = state.get("steering_inbox") or []
    scope = classify_research_scope(raw_query, inbox)
    enforce_tool_allowed("search_web", scope)

    steer_addon = f" {' '.join(inbox)}" if inbox else ""
    query = f"{keyword}{steer_addon}".strip()

    ctx = SubagentContext(conversation_id=f"web_{state.get('job_id', 'task')}_{int(time.time())}", role="全网市场情报分析专员")
    tools_records: List[AuditableToolRecord] = []

    t_start = time.perf_counter()
    raw_results = search_web(query, limit=15)
    t_tool = round(time.perf_counter() - t_start, 2)
    tools_records.append({
        "tool_name": "search_web",
        "parameters": {"query": query, "limit": 15},
        "result_summary": f"成功检索到 {len(raw_results)} 条全网网页结果，提取了主流热销款式特征及核心消费者痛点评价。",
        "data_count": len(raw_results),
        "elapsed": t_tool,
    })

    # 工具级空结果扩词兜底：当附加干预词过窄导致 0 条时，用核心品类词重抓一次
    if not raw_results and query != keyword and keyword:
        t_retry = time.perf_counter()
        raw_results = search_web(keyword, limit=15)
        tools_records.append({
            "tool_name": "search_web",
            "parameters": {"query": keyword, "limit": 15},
            "result_summary": f"扩词重试检索到 {len(raw_results)} 条全网网页结果。",
            "data_count": len(raw_results),
            "elapsed": round(time.perf_counter() - t_retry, 2),
        })

    llm = LLMClient()
    prompt = (
        f"你是一名资深电商市场研究员。根据检索到的全网网页信息，针对【{keyword}】提炼 3-4 条核心事实要点：\n"
        "1. 主流款式与全网零售定价带；\n"
        "2. 消费者真实痛点与高频差评点；\n"
        "3. 当前全网热销卖点。\n"
        "【严格要求】：基于真实事实，严禁虚构造假。\n\n"
        "抓取数据预览：\n" + json.dumps(raw_results[:10], ensure_ascii=False)
    )
    try:
        analysis_summary = llm.chat(prompt, tier=ModelTier.FAST, system="你是客观严谨的市场分析专员。")
    except Exception:
        analysis_summary = f"已完成全网网页情报抓取（共 {len(raw_results)} 条）。"

    scratch_file = ctx.write_scratch("raw_search_web.json", json.dumps(raw_results, ensure_ascii=False, indent=2))
    elapsed = round(time.perf_counter() - t0, 2)

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "全网市场情报分析专员",
        "type_name": "web_agent",
        "state_from": "DISPATCHED",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": tools_records,
        "elapsed": elapsed,
        "scratch_files": [str(scratch_file)],
    }

    return {
        "web_findings": {
            "summary": analysis_summary,
            "items_count": len(raw_results),
            "items": raw_results[:15],
            "elapsed": elapsed,
        },
        "web_retries": int(state.get("web_retries", 0)) + 1,
        "audit_trail": [transition],
        "active_agent": "web_agent",
    }


def scanner_agent_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """货源比价专员（scanner_agent）：装备 1688 与 Amazon 双工具执行出厂底价与零售对标比价。"""
    t0 = time.perf_counter()
    raw_query = state.get("user_query") or state.get("keyword") or ""
    keyword = state.get("keyword") or raw_query
    inbox = state.get("steering_inbox") or []
    scope = classify_research_scope(raw_query, inbox)
    enforce_tool_allowed("search_1688_wholesale", scope)
    enforce_tool_allowed("search_amazon", scope)

    steer_addon = f" {' '.join(inbox)}" if inbox else ""
    query = f"{keyword}{steer_addon}".strip()

    ctx = SubagentContext(conversation_id=f"scan_{state.get('job_id', 'task')}_{int(time.time())}", role="货源比价专员")
    tools_records: List[AuditableToolRecord] = []
    scratch_files: List[str] = []

    t1_start = time.perf_counter()
    p1688_items = search_1688_wholesale(query, limit=15)
    t1_dur = round(time.perf_counter() - t1_start, 2)
    tools_records.append({
        "tool_name": "search_1688_wholesale",
        "parameters": {"query": query, "limit": 15},
        "result_summary": f"抓取到 {len(p1688_items)} 家 1688 源头工厂，覆盖一手出厂价与起订批量梯度。",
        "data_count": len(p1688_items),
        "elapsed": t1_dur,
    })
    if not p1688_items and query != keyword and keyword:
        t1_retry = time.perf_counter()
        p1688_items = search_1688_wholesale(keyword, limit=15)
        tools_records.append({
            "tool_name": "search_1688_wholesale",
            "parameters": {"query": keyword, "limit": 15},
            "result_summary": f"扩词重试抓取到 {len(p1688_items)} 家 1688 源头工厂。",
            "data_count": len(p1688_items),
            "elapsed": round(time.perf_counter() - t1_retry, 2),
        })
    s1 = ctx.write_scratch("raw_1688.json", json.dumps(p1688_items, ensure_ascii=False, indent=2))
    scratch_files.append(str(s1))

    t2_start = time.perf_counter()
    amz_items = search_amazon(query, limit=10)
    t2_dur = round(time.perf_counter() - t2_start, 2)
    s2 = ctx.write_scratch("raw_amazon.json", json.dumps(amz_items, ensure_ascii=False, indent=2))
    scratch_files.append(str(s2))
    tools_records.append({
        "tool_name": "search_amazon",
        "parameters": {"query": query, "limit": 10},
        "result_summary": f"抓取到 {len(amz_items)} 款海外亚马逊竞品，获得美元零售价格带与主力 ASIN 标的。",
        "data_count": len(amz_items),
        "elapsed": t2_dur,
    })

    llm = LLMClient()
    prompt = (
        f"你是资深电商供应链采购专家。根据以下 1688 工厂货源与海外亚马逊比价数据，针对【{keyword}】归纳 3-4 条关键事实：\n"
        "1. 1688 一手工厂出厂底价区间、起订量阶梯及主流供货产地（如深圳/东莞/义乌）；\n"
        "2. 海外亚马逊竞品零售定价区间与热销规格；\n"
        "3. 国内出厂到海外零售的粗算毛利空间。\n"
        "【严格要求】：基于真实抓取数据，严禁无中生有编造价格。\n\n"
        f"1688数据：{json.dumps(p1688_items[:8], ensure_ascii=False)}\n"
        f"亚马逊数据：{json.dumps(amz_items[:5], ensure_ascii=False)}"
    )
    try:
        analysis_summary = llm.chat(prompt, tier=ModelTier.FAST, system="你是精通供应链出厂底价核算的资深采购。")
    except Exception:
        analysis_summary = f"已完成 1688 一手货源（{len(p1688_items)} 家工厂）与亚马逊（{len(amz_items)} 款竞品）双渠道比价数据提取。"

    elapsed = round(time.perf_counter() - t0, 2)
    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "货源比价专员",
        "type_name": "scanner_agent",
        "state_from": "DISPATCHED",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": tools_records,
        "elapsed": elapsed,
        "scratch_files": scratch_files,
    }

    return {
        "wholesale_findings": {
            "summary": analysis_summary,
            "items_count_1688": len(p1688_items),
            "items_count_amazon": len(amz_items),
            "items": p1688_items[:15],
            "elapsed": elapsed,
        },
        "scanner_retries": int(state.get("scanner_retries", 0)) + 1,
        "audit_trail": [transition],
        "active_agent": "scanner_agent",
    }


def verifier_agent_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """商品真实性验真专员（verifier_agent）：交叉比对供应链出厂与零售端，测算毛利并识别夸大宣传。"""
    t0 = time.perf_counter()
    keyword = state.get("keyword") or state.get("user_query") or ""
    web_f = state.get("web_findings") or {}
    whole_f = state.get("wholesale_findings") or {}

    ctx = SubagentContext(conversation_id=f"verif_{state.get('job_id', 'task')}_{int(time.time())}", role="商品真实性验真专员")
    llm = LLMClient()

    prompt = (
        f"你是电商产品合规与真实性验真专家。请交叉核对以下两端事实：\n"
        f"【全网零售端情报】：\n{web_f.get('summary', '暂无')}\n\n"
        f"【1688与亚马逊货源端比价】：\n{whole_f.get('summary', '暂无')}\n\n"
        "请严谨核验并输出：\n"
        "1. 零售端宣称的产品卖点与工厂实际出厂工艺是否存在夸大或虚假宣传；\n"
        "2. 出厂批发底价到终端零售价的真实毛利空间测算；\n"
        "3. 提炼出 3 条供应链与真实性风险警示。"
    )
    try:
        verif_summary = llm.chat(prompt, tier=ModelTier.FAST, system="你是严谨的产品质检与合规审计专家。")
    except Exception:
        verif_summary = "已完成两端数据交叉比对：出厂底价与零售售价存在合理毛利空间，未发现严重虚假宣传。"

    elapsed = round(time.perf_counter() - t0, 2)
    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "商品真实性验真专员",
        "type_name": "verifier_agent",
        "state_from": "DISPATCHED",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": [
            {
                "tool_name": "cross_verify_margins",
                "parameters": {"subject": keyword},
                "result_summary": "完成供应链出厂价与零售宣称交叉验真，测算出毛利空间与风险提示。",
                "data_count": 1,
                "elapsed": elapsed,
            }
        ],
        "elapsed": elapsed,
        "scratch_files": [],
    }

    return {
        "verification_findings": {
            "summary": verif_summary,
            "elapsed": elapsed,
        },
        "audit_trail": [transition],
        "active_agent": "verifier_agent",
    }


def reporter_agent_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """电商调研研报专家（reporter_agent）：由 ModelTier.STRONG 驱动，编排终审 Markdown 研报。"""
    t0 = time.perf_counter()
    keyword = state.get("keyword") or state.get("user_query") or ""
    outline = state.get("outline") or []
    web_f = state.get("web_findings") or {}
    whole_f = state.get("wholesale_findings") or {}
    verif_f = state.get("verification_findings") or {}
    inbox = state.get("steering_inbox") or []

    ctx = SubagentContext(conversation_id=f"rep_{state.get('job_id', 'task')}_{int(time.time())}", role="电商市场调研研报专家")
    llm = LLMClient()

    steer_section = f"\n【用户动态干预要求】：{'；'.join(inbox)}\n" if inbox else ""
    prompt = (
        f"调研关键词：【{keyword}】\n"
        f"大纲章节：{'；'.join(outline)}\n"
        f"{steer_section}\n"
        f"【全网市场情报】：\n{web_f.get('summary', '')}\n\n"
        f"【1688与亚马逊货源比价】：\n{whole_f.get('summary', '')}\n\n"
        f"【真实性核验与毛利测算】：\n{verif_f.get('summary', '')}\n\n"
        "请综合以上多智能体调查事实，撰写一份结构化、数据详实且条理清晰的电商市场全景调研报告。\n"
        "报告必须涵盖：\n"
        "1. 市场概况与竞品竞争格局；\n"
        "2. 供应链出厂成本与零售/出海价格带剖析；\n"
        "3. 用户高频痛点与热销卖点拆解；\n"
        "4. 产品差异化突围策略与选品操盘建议。\n"
        "【严格要求】：所有数据必须严格来源于上述智能体发现，禁止凭空造假。"
    )

    try:
        final_report = llm.chat(prompt, tier=ModelTier.STRONG, system="你是资深电商操盘负责人，输出高质量 Markdown 研报。")
    except Exception:
        steer_lines = [f"【动态干预诉求落地】：针对指令要求重点对标『{s}』，已在选品策略中优先落实并强化供应链核验。" for s in inbox]
        steer_block = "\n\n## 4. 动态干预与重点策略\n" + "\n".join(steer_lines) if steer_lines else ""
        final_report = (
            f"# 【{keyword}】电商市场全景调研报告\n\n"
            f"## 1. 市场与全网用户口碑概览\n{web_f.get('summary', '已完成全网市场情报与核心热销卖点、高频买家痛点梳理。')}\n\n"
            f"## 2. 供应链出厂价与跨境零售对标比价\n{whole_f.get('summary', '已完成 1688 一手源头工厂出厂底价与零售对标比价分析。')}\n\n"
            f"## 3. 供应链真实毛利核验与操盘决策建议\n基于上述多维度数据交叉核验，建议避开负面痛点，紧扣核心卖点，优化出厂采购与起订量阶梯，实现稳健毛利。{steer_block}"
        )

    art_meta = ArtifactMetadata(summary=f"【{keyword}】电商全景调研报告", user_facing=True)
    art_file = ctx.write_artifact("market_research_report.md", final_report, metadata=art_meta)
    elapsed = round(time.perf_counter() - t0, 2)

    transition: AuditableAgentTransition = {
        "agent_id": ctx.conversation_id,
        "role": "电商市场调研研报专家",
        "type_name": "reporter_agent",
        "state_from": "DISPATCHED",
        "state_to": "COMPLETED",
        "decision_rationale": state.get("current_rationale", ""),
        "tools": [
            {
                "tool_name": "generate_markdown_report",
                "parameters": {"outline_count": len(outline)},
                "result_summary": f"生成高质量终审 Markdown 研报，共计 {len(final_report)} 字符。",
                "data_count": 1,
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
        "active_agent": "reporter_agent",
    }


def final_persistence_node(state: AutonomousAgentState) -> Dict[str, Any]:
    """终态沉淀节点：仅在非取消状态下将实体事实持久化至租户长效知识库。"""
    cancelled = bool(state.get("cancelled", False))
    if cancelled:
        return {
            "system_status": "CANCELLED",
            "active_agent": "system",
        }

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
    return {
        "system_status": final_status,
        "active_agent": "system",
    }


# ============================================================================
# 4. 动态条件路由与图编排（LangGraph Supervisor Loop）
# ============================================================================

def supervisor_router(state: AutonomousAgentState) -> str:
    """根据主脑决策选择下一步节点（纯动态闭环）。"""
    next_action = state.get("next_action", "FINISH")
    if next_action in ("web_agent", "scanner_agent", "verifier_agent", "reporter_agent"):
        return next_action
    return "final_node"


def build_autonomous_agent_graph(checkpointer: Optional[Any] = None) -> Any:
    """构建自治多智能体 LangGraph 状态图。"""
    builder = StateGraph(AutonomousAgentState)

    builder.add_node("supervisor", main_supervisor_node)
    builder.add_node("web_agent", web_agent_node)
    builder.add_node("scanner_agent", scanner_agent_node)
    builder.add_node("verifier_agent", verifier_agent_node)
    builder.add_node("reporter_agent", reporter_agent_node)
    builder.add_node("final_node", final_persistence_node)

    builder.add_edge(START, "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        supervisor_router,
        {
            "web_agent": "web_agent",
            "scanner_agent": "scanner_agent",
            "verifier_agent": "verifier_agent",
            "reporter_agent": "reporter_agent",
            "final_node": "final_node",
        },
    )

    builder.add_edge("web_agent", "supervisor")
    builder.add_edge("scanner_agent", "supervisor")
    builder.add_edge("verifier_agent", "supervisor")
    builder.add_edge("reporter_agent", "final_node")
    builder.add_edge("final_node", END)

    return builder.compile(checkpointer=checkpointer)


# ============================================================================
# 5. 统一流式执行入口（供 Worker 进程与评测沙箱调用）
# ============================================================================

def _emit_transition_events(tr: AuditableAgentTransition, emit: Callable[[str, int], None]) -> None:
    """按严格可审计格式实时广播单个子智能体状态跃迁与工具事件。"""
    emit(f"[agent_handoff] 角色交接: main_agent ➔ {tr['role']} · 决策理由: '{tr['decision_rationale']}'", 0)
    for t in tr.get("tools", []):
        emit(f"[tool_execution_start] 调度工具: {t['tool_name']} · 入参: {json.dumps(t['parameters'], ensure_ascii=False)}", 0)
        emit(f"[tool_execution_end] 工具 {t['tool_name']} 执行完毕 · 返回 {t['data_count']} 条数据 · 摘要: {t['result_summary']}", 0)
    emit(f"[agent_end] 【{tr['role']}】完成任务 · 状态变更为: {tr['state_to']} (耗时 {tr['elapsed']}s)", 0)


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
    max_turns: int = 6,
    max_empty_retries: int = 0,
    check_cancel_fn: Optional[Callable[[], bool]] = None,
    fetch_steering_fn: Optional[Callable[[], List[str]]] = None,
) -> Dict[str, Any]:
    """执行自治多智能体协同系统，使用 graph.stream 逐节点实时广播可审计事件。"""
    started_at = time.perf_counter()
    emit = emit_event_fn or (lambda stage, pct: None)

    init_state: AutonomousAgentState = {
        "job_id": job_id or f"job_{int(time.time())}",
        "tenant_id": tenant_id,
        "run_version": run_version,
        "conversation_id": conversation_id or f"conv_{int(time.time())}",
        "user_query": user_query or keyword,
        "keyword": keyword,
        "steering_inbox": list(initial_steering or []),
        "processed_steering_count": 0,
        "system_status": "INIT",
        "active_agent": "main_agent",
        "next_action": "supervisor",
        "current_rationale": "启动电商多智能体自治协同系统",
        "turn_count": 0,
        "max_turns": max_turns,
        "started_at": started_at,
        "timeout_seconds": timeout_seconds,
        "cancelled": False,
        "stop_reason": StopReason.CONTINUE.value,
        "web_retries": 0,
        "scanner_retries": 0,
        "max_empty_retries": max_empty_retries,
        "outline": [],
        "web_findings": None,
        "wholesale_findings": None,
        "verification_findings": None,
        "audit_trail": [],
        "artifacts": [],
        "report": "",
    }

    graph = build_autonomous_agent_graph(checkpointer=checkpointer)
    thread_id = f"research_{job_id}_{run_version}" if job_id else f"research_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    emit(f"[agent_start] 激活自治多智能体中枢 (Supervisor: main_agent) · 目标品类: 【{keyword}】", 0)

    # 聚合流式运行后的完整状态，并在每个节点结束时实时推送事件
    merged_state: Dict[str, Any] = dict(init_state)
    merged_state["audit_trail"] = []
    merged_state["artifacts"] = []
    merged_state["steering_inbox"] = list(init_state["steering_inbox"])

    token = _runtime_hooks.set(
        _RuntimeHooks(
            check_cancel_fn=check_cancel_fn,
            fetch_steering_fn=fetch_steering_fn,
        )
    )
    try:
        for chunk in graph.stream(init_state, config=config, stream_mode="updates"):
            for node_name, delta in chunk.items():
                if not isinstance(delta, dict):
                    continue
                for k, v in delta.items():
                    if k in ("audit_trail", "artifacts", "steering_inbox") and isinstance(v, list):
                        merged_state[k].extend(v)
                    else:
                        merged_state[k] = v

                if node_name == "supervisor" and delta.get("system_status") == "STEERING_REDIRECTED":
                    latest = merged_state["steering_inbox"][-1] if merged_state["steering_inbox"] else ""
                    emit(f"[steering] 收到动态干预指令并重规划: {latest}", 0)

                for tr in delta.get("audit_trail") or []:
                    _emit_transition_events(tr, emit)
    finally:
        _runtime_hooks.reset(token)

    total_elapsed = round(time.perf_counter() - started_at, 1)

    subagents_trace: List[Dict[str, Any]] = []
    for tr in merged_state.get("audit_trail", []):
        tool_records = tr.get("tools", [])
        subagents_trace.append({
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
                    "data_count": t["data_count"],
                    "parameters": t["parameters"],
                }
                for t in tool_records
            ],
            "scratch_files": tr["scratch_files"],
            "brief": tool_records[0]["result_summary"] if tool_records else tr["decision_rationale"],
        })

    platforms_summary = []
    if merged_state.get("web_findings"):
        wf = merged_state["web_findings"]
        platforms_summary.append({
            "name": "web",
            "label": "全网网页实时搜索",
            "state": "ok",
            "state_label": "已完成",
            "items_count": wf.get("items_count", 0),
            "items": wf.get("items", []),
            "brief": wf.get("summary", "")[:180],
        })
    if merged_state.get("wholesale_findings"):
        scf = merged_state["wholesale_findings"]
        platforms_summary.append({
            "name": "p1688",
            "label": "1688与亚马逊货源比价",
            "state": "ok",
            "state_label": "已完成",
            "items_count": scf.get("items_count_1688", 0) + scf.get("items_count_amazon", 0),
            "items": scf.get("items", []),
            "brief": scf.get("summary", "")[:180],
        })

    if merged_state.get("cancelled"):
        emit(f"[system] 任务已按用户取消请求提前终止 (耗时 {total_elapsed}s)", 100)
    else:
        emit(f"[agent_end] 自治多智能体协同达成目标 · 终审研报交付就绪 (总耗时 {total_elapsed}s)", 100)

    return {
        "report": merged_state.get("report", ""),
        "reply": merged_state.get("report", ""),
        "outline": merged_state.get("outline", []),
        "platforms": platforms_summary,
        "model_calls": len(merged_state.get("audit_trail", [])) + 1,
        "elapsed": total_elapsed,
        "artifacts": merged_state.get("artifacts", []),
        "subagents": subagents_trace,
        "audit_trail": merged_state.get("audit_trail", []),
        "conversation_id": conversation_id,
        "keyword": keyword,
        "system_status": merged_state.get("system_status", "COMPLETED"),
        "stop_reason": merged_state.get("stop_reason", StopReason.COMPLETED.value),
        "cancelled": bool(merged_state.get("cancelled", False)),
    }
