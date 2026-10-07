"""Supervisor（商品知识问答主管）：意图识别 → 任务拆解 → 路由调度。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ..llm import ModelTier, get_llm
from ..llm.json_utils import extract_json
from ..observability import log_event
from ..schemas.research import ResearchDepth, ResearchTask, TaskPlan
from .state import ResearchRuntime, ResearchState, append_trace

_PLAN_PROMPT = """你是电商商品知识问答系统的【任务规划器】。请对下面的用户问题做意图识别与任务拆解。

用户问题：{question}
商品类目：{category}   品牌：{brand}   产品线：{product_line}   深度：{depth}

【会话历史】（用于「它 / 该型号 / 那款」等指代消解；为「（无）」表示本轮是会话首轮）
{history}

意图类别（intent）从以下四类中选一：
- spec_query：商品参数查询 —— 单个型号的参数 / 规格 / 功能口径
  （示例："MC-500 切幅多少""C3 防窥膜的防窥角度是多少"）；
  推荐 target_sources：[spec_sheet, product_page, manual]
- sku_compare：型号对比 —— 两个及以上 SKU 的参数差异与横向对比
  （示例："MC-500 和 MC-300 有什么区别""UV-600 和 UV-900 怎么选"）；
  推荐 target_sources：[spec_sheet, product_page, local_corpus]
- compat_recommend：适配推荐 —— 膜 / 壳 / 配件与机型的适配关系与替代方案
  （示例："iPhone 16 Pro Max 贴哪款膜""T20 壳支持哪些机型""C5 能不能用在 Mate X5 上"）；
  推荐 target_sources：[manual, spec_sheet, faq]
- after_sales：售后问答 —— 保修退换政策 / 故障排查 / 使用教程
  （示例："钢化膜起泡怎么办""UV 打印机堵头""切膜机连不上蓝牙"）；
  推荐 target_sources：[faq, manual, tutorial]

可选资料源 target_sources 从 [product_page, spec_sheet, manual, tutorial, faq, local_corpus] 中选择。

同时输出：
- sub_questions：3-5 个可并行取证的子问题
- target_sources：与意图匹配的资料源列表
- focus_areas：涉及的产品线 / 类目关键词
- rationale：一句话拆解理由

输出 JSON：{{"intent":"spec_query","sub_questions":[""],"target_sources":["spec_sheet"],
"focus_areas":[""],"rationale":""}}
"""

_REWRITE_PROMPT = """用户问题：{question}
已完成的取证轮次：{round}
当前未收敛的核验假设：
{hypotheses}

请为每个未收敛假设生成 1 条更精准的检索查询（用于下一轮取证），
输出 JSON：{{"queries":["",""]}}
"""


def supervisor_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    task: ResearchTask = state["task"]
    started = time.perf_counter()

    plan = state.get("plan")
    if plan is None:
        plan = _make_plan(state, task, runtime)
    else:
        # 后续轮次：为未收敛假设重写检索查询
        if state.get("research_round", 0) > 0:
            plan = _refine_plan(task, state, runtime, plan)

    route = decide_route(state)
    # 安全护栏：防止编排死循环
    if state.get("iteration", 0) >= runtime.settings.max_agent_iterations:
        route = "report_generation"
    append_trace(state, "supervisor", f"intent={plan.intent} -> {route}",
                 time.perf_counter() - started)
    log_event("supervisor_route", task_id=task.task_id, intent=plan.intent, route=route)

    return {
        "plan": plan,
        "route": route,
        "iteration": state.get("iteration", 0) + 1,
        "trace": state.get("trace", []),
    }


def _make_plan(state: ResearchState, task: ResearchTask,
               runtime: ResearchRuntime) -> TaskPlan:
    if not runtime.settings.has_llm or task.depth == ResearchDepth.QUICK:
        context = [x for x in (task.product_line, task.category, task.brand) if x]
        return TaskPlan(
            intent=_rule_intent(task.question),
            sub_questions=[task.question] + context[:3],
            target_sources=["local_corpus", "product_page", "manual_source"],
            focus_areas=context or ["全品类"],
            rationale="快速任务规则路由（复杂任务交由模型规划）",
        )
    # 注入会话历史（【会话历史】文本块），供「它 / 该型号」等指代消解
    history = str(state.get("session_history", "") or "").strip() or "（无）"
    prompt = _PLAN_PROMPT.format(
        question=task.question,
        category=task.category or "未指定",
        brand=task.brand or "未指定",
        product_line=task.product_line or "未指定",
        depth=task.depth.value if hasattr(task.depth, "value") else str(task.depth),
        history=history[:2000],
    )
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.STRONG, max_tokens=1200)
        data = extract_json(raw, expect="object") or {}
    except Exception:  # noqa: BLE001
        data = {}
    context = [x for x in (task.product_line, task.category) if x]
    return TaskPlan(
        intent=str(data.get("intent", "") or _rule_intent(task.question)),
        sub_questions=[str(x) for x in (data.get("sub_questions") or [])][:5]
        or [task.question],
        target_sources=[str(x) for x in (data.get("target_sources") or [])][:5]
        or ["local_corpus", "product_page", "manual_source"],
        focus_areas=[str(x) for x in (data.get("focus_areas") or [])][:6]
        or context,
        rationale=str(data.get("rationale", ""))[:300],
    )


def _refine_plan(task: ResearchTask, state: ResearchState,
                 runtime: ResearchRuntime, plan: TaskPlan) -> TaskPlan:
    pending = [
        h for h in state.get("hypotheses", [])
        if h.status.value in ("pending", "investigating", "inconclusive")
    ]
    if not pending or not runtime.settings.has_llm:
        return plan
    prompt = _REWRITE_PROMPT.format(
        question=task.question,
        round=state.get("research_round", 0),
        hypotheses="\n".join(f"- {h.hypothesis_text}" for h in pending[:5]),
    )
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=800)
        data = extract_json(raw, expect="object") or {}
        queries = [str(q) for q in (data.get("queries") or [])][:5]
    except Exception:  # noqa: BLE001
        queries = []
    if queries:
        plan.sub_questions = queries or plan.sub_questions
    return plan


def _rule_intent(question: str) -> str:
    """关键词规则路由：sku_compare / compat_recommend / after_sales / spec_query。"""
    if any(k in question for k in ("区别", "对比", "差异", "哪个好")):
        return "sku_compare"
    if any(k in question for k in ("适配", "兼容", "能不能用", "通用", "适用", "贴", "支持哪些机型")):
        return "compat_recommend"
    if any(k in question for k in ("售后", "保修", "退货", "故障", "维修", "坏了", "怎么办",
                                   "起泡", "堵头", "切歪", "蓝牙", "碎边", "发黄")):
        return "after_sales"
    if any(k in question for k in ("参数", "规格", "切幅", "透光率", "防窥", "刀模", "喷头")):
        return "spec_query"
    return "spec_query"


def decide_route(state: ResearchState) -> str:
    """根据已完成情况决定下一个 Agent。"""
    task: ResearchTask = state["task"]
    if state.get("errors") and not state.get("documents"):
        return "end"

    if (state.get("graph_result") or {}).get("mode") == "retrieval_first" and not state.get("documents"):
        if state.get("report") is not None:return "end"
        return "specialist_review" if not state.get("specialists") else "report_generation"
    if not state.get("documents"):
        return "data_collection"
    if not state.get("chunks"):
        return "doc_parsing"
    if state.get("extraction") is None:
        return "knowledge_extraction"
    if not state.get("graph_result"):
        return "graph_building"
    if state.get("report") is not None:
        return "end"

    # 研判阶段
    max_rounds = _max_rounds(task)
    if state.get("research_round", 0) < max_rounds and _needs_more_research(state):
        return "deep_research"
    return "specialist_review" if not state.get("specialists") else "report_generation"


def _needs_more_research(state: ResearchState) -> bool:
    hypotheses: List[Any] = state.get("hypotheses", []) or []
    if not hypotheses:
        return True
    unresolved = [
        h for h in hypotheses
        if h.status.value in ("pending", "investigating", "inconclusive")
    ]
    return len(unresolved) > 0


def _max_rounds(task: ResearchTask) -> int:
    depth = task.depth.value if hasattr(task.depth, "value") else str(task.depth)
    return {
        ResearchDepth.QUICK.value: 0,
        ResearchDepth.STANDARD.value: 2,
        ResearchDepth.DEEP.value: 4,
    }.get(depth, 2)


def route_next(state: ResearchState) -> str:
    """LangGraph 条件边入口。"""
    route = state.get("route", "end")
    return {
        "data_collection": "data_collection",
        "doc_parsing": "doc_parsing",
        "knowledge_extraction": "knowledge_extraction",
        "graph_building": "graph_building",
        "deep_research": "deep_research",
        "report_generation": "report_generation",
        "specialist_review": "specialist_review",
    }.get(route, "end")
