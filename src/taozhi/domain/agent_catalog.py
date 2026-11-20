"""将业务角色编号转换为界面使用的中文名称。"""
from taozhi.domain.rag_verification import SPECIALIST_AGENTS


_AGENT_NAMES = {
    **{role_id: name for role_id, (name, _) in SPECIALIST_AGENTS.items()},
    "research.orchestrator": "调研协调员",
    "research.subagent_worker": "市场调研员",
    "research.synthesizer": "调研报告分析师",
    "research.market_intelligence": "市场情报分析师",
    "research.sourcing": "1688 采购分析师",
    "research.competitive_pricing": "亚马逊竞品分析师",
    "semantic_cache": "已核验答案缓存",
}


def agent_display_name(role: str) -> str:
    """为注册的角色编号提供展示名称，原样呈现其他记录自身的文本。"""
    return _AGENT_NAMES.get(role, role)
