"""企业知识问答规划与多专家路由选择节点。"""
from __future__ import annotations

import re

from taozhi.application.common.task_guard import empty_token_usage, validate_task_execution
from taozhi.domain.privacy import redact
from taozhi.domain.rag_verification import SPECIALIST_AGENTS
from taozhi.orchestration.decision import get_decision_engine
from .state import State

_SPECIALIST_PATTERNS = [
    ("product", ["规格", "参数", "型号", "尺寸", "切幅", "材质", "材质说明", "功能", "兼容", "区别", "对比"]),
    ("after_sales", ["售后", "退货", "保修", "保修期", "换新", "运费", "维修", "投诉"]),
    ("operations", ["主图", "文案", "卖点", "上架", "违禁词", "详情页"]),
    ("supply", ["库存", "发货", "起订", "供货", "现货", "交期"]),
    ("finance", ["毛利", "成本", "底价", "结算"]),
]


def plan_node(state: State) -> dict:
    """意图理解与多智能体路由规划（零外部模型调用）。"""
    _, job = validate_task_execution(state, "动态意图理解与多智能体路由规划", 10)
    question = job["question"]
    options = {role_id: f"{title}：{guidance}" for role_id, (title, guidance) in SPECIALIST_AGENTS.items()}
    decision = get_decision_engine().choice(
        context=redact(question),
        options=options,
        multi_select=False,
        top_k=3,
        force_llm=False,
        allow_llm=False,
    )
    selected_agents = [role_id for role_id in decision.selected if role_id in SPECIALIST_AGENTS]
    for role, patterns in _SPECIALIST_PATTERNS:
        if any(keyword in question for keyword in patterns):
            selected_agents.append(role)
    if not selected_agents or re.search("规格|尺寸|型号|参数|幅宽|切幅|材质", question):
        selected_agents.insert(0, "product")
    selected_agents = list(dict.fromkeys(selected_agents))[:3]

    return dict(
        question=question,
        selected_agents=selected_agents or ["product"],
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
