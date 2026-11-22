"""企业知识问答规划与多专家路由选择节点。"""
from __future__ import annotations

from taozhi.application.common.task_guard import empty_token_usage, validate_task_execution
from taozhi.domain.privacy import redact
from taozhi.domain.question_context import business_field_requests
from taozhi.domain.rag_verification import SPECIALIST_AGENTS
from taozhi.orchestration.decision import DecisionPath, get_decision_engine
from .state import State

_SPECIFICATION_TERMS = ("规格", "参数", "型号", "尺寸", "切幅", "幅宽", "材质")
_ROLE_TERMS = {
    "product": (*_SPECIFICATION_TERMS, "功能", "区别", "对比"),
    "compatibility": ("兼容", "适配", "通用", "贴合", "顶膜"),
    "after_sales": ("售后", "退货", "保修", "换新", "运费", "维修", "投诉"),
    "coach": ("培训", "带教", "教学", "操作规程", "操作步骤", "故障排查", "排障"),
    "operations": ("主图", "文案", "卖点", "上架", "违禁词", "详情页"),
    "supply": ("库存", "发货", "起订", "供货", "现货", "交期"),
    "finance": ("财务", "毛利", "利润", "成本", "底价", "结算"),
}


def _select_specialists(question: str) -> list[str]:
    options = {role_id: f"{title}：{guidance}" for role_id, (title, guidance) in SPECIALIST_AGENTS.items()}
    decision = get_decision_engine().choice(
        context=redact(question),
        options=options,
        allow_llm=False,
    )
    matched = [role for role, terms in _ROLE_TERMS.items() if any(term in question for term in terms)]
    selected = []
    if any(term in question for term in _SPECIFICATION_TERMS):
        selected.append("product")
    if decision.path == DecisionPath.FAST_SEMANTIC or not matched:
        selected.extend(role for role in decision.selected if role in SPECIALIST_AGENTS)
    selected.extend(matched)
    return list(dict.fromkeys(selected))[:3] or ["product"]


def plan_node(state: State) -> dict:
    """为实时业务字段选择权威系统，知识问题按主题分派专家。"""
    _, job = validate_task_execution(state, "识别问题与答复依据", 10)
    question = job["question"]
    live_fields = business_field_requests(question)
    return dict(
        question=question,
        live_fields=live_fields,
        selected_agents=[] if live_fields else _select_specialists(question),
        model_calls=0,
        missing=[],
        findings=[],
        sources=[],
        business=[],
        usage=[],
        input_level=job["input_level"],
        timing={},
        degraded={},
        token_usage=empty_token_usage(),
    )
