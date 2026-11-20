"""知识问答与选品调研角色目录。角色编号用于关联调用，中文名称用于呈现。"""
from __future__ import annotations

from taozhi.domain.rag_verification import SPECIALIST_AGENTS


_KNOWLEDGE_ROLES = {
    "product": "商品规格分析师",
    "compatibility": "产品兼容性分析师",
    "after_sales": "售后政策顾问",
    "coach": "产品培训顾问",
    "operations": "商品运营分析师",
    "supply": "库存与供应分析师",
    "finance": "经营财务分析师",
}

# 每项的首个名称是当前名称，其余是已写入审计账本或历史任务的名称。
_RESEARCH_ROLES = {
    "research.orchestrator": ("调研协调员", "调研主协调器"),
    "research.subagent_worker": ("市场调研员", "市场调研专员"),
    "research.synthesizer": ("调研报告分析师", "终审研报合成专家", "调研报告合成"),
    "research.market_intelligence": ("市场情报分析师", "全网口碑与市场情报专员", "全网痛点研究员", "全网市场情报分析专员"),
    "research.sourcing": ("1688 采购分析师", "1688源头工厂采购专员", "1688货源采购专员"),
    "research.competitive_pricing": ("亚马逊竞品分析师", "亚马逊跨境比价专员", "Amazon对标专员"),
}
_RESEARCH_ROLE_IDS = {
    alias: role_id for role_id, names in _RESEARCH_ROLES.items() for alias in (role_id, *names)
}
_KNOWLEDGE_ROLE_IDS = {terms[0]: role_id for role_id, terms in SPECIALIST_AGENTS.items()}


def research_agent_id(role: str) -> str:
    """为遥测分配低基数角色编号，自定义任务角色归入市场调研员。"""
    return _RESEARCH_ROLE_IDS.get(role, "research.subagent_worker")


def agent_display_name(role: str) -> str:
    """统一当前及历史角色名称，保留调研计划自定义的业务角色。"""
    role = role.removeprefix("调研子智能体 · ").strip()
    if role in {"Main 调度 / 语义缓存", "semantic_cache"}:
        return "已核验答案缓存"
    if role in _RESEARCH_ROLE_IDS:
        return _RESEARCH_ROLES[_RESEARCH_ROLE_IDS[role]][0]
    return _KNOWLEDGE_ROLES.get(_KNOWLEDGE_ROLE_IDS.get(role, role), role)
