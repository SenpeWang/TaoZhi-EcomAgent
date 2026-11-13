"""子智能体类型定义与统一注册中心（应用层智能体中台）。

管理专职电商子智能体的内置角色模板、绑定工具集与系统指令。
严格遵循 Google Python Style 规范。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional
from .protocol import SubagentDefinition
from ..tools.search_web import search_web
from ..tools.search_1688 import search_1688_wholesale
from ..tools.search_amazon import search_amazon


class SubagentRegistry:
    """子智能体类型注册表，管理系统所有可用角色模板并内置电商 5 大核心专职智能体。"""

    def __init__(self) -> None:
        self._definitions: Dict[str, SubagentDefinition] = {}
        self._register_builtins()

    def define_subagent(
        self,
        name: str,
        role: str,
        description: str,
        system_prompt: str,
        tools: Optional[List[Callable]] = None,
        model_tier: str = "fast",
        enable_write: bool = True,
        enable_subagent: bool = False,
    ) -> SubagentDefinition:
        """动态注册一个新的子智能体类型。"""
        definition = SubagentDefinition(
            name=name,
            role=role,
            description=description,
            system_prompt=system_prompt,
            tools=tools or [],
            model_tier=model_tier,
            enable_write=enable_write,
            enable_subagent=enable_subagent,
        )
        self._definitions[name] = definition
        return definition

    def get(self, name: str) -> Optional[SubagentDefinition]:
        """按名称获取已注册的智能体定义，支持简写或全称。"""
        if name in self._definitions:
            return self._definitions[name]
        alt_name = f"{name}_agent" if not name.endswith("_agent") else name[:-6]
        return self._definitions.get(alt_name)

    def list_all(self) -> List[SubagentDefinition]:
        """列出所有已注册的智能体类型。"""
        return list(self._definitions.values())

    def _register_builtins(self) -> None:
        """注册内置的电商调研子智能体家族。"""
        # 1. 调研协同总指挥 (main_agent / main)
        self.define_subagent(
            name="main_agent",
            role="调研协同总指挥",
            description="负责解析用户调研诉求、制定调研大纲、协同派发工作区与多智能体并行调度。",
            system_prompt=(
                "你是自研电商多智能体中台的协同总指挥。你的任务是统筹全景调研：\n"
                "1. 分解品类调研维度（市场、价格带、供应链、差异化）；\n"
                "2. 组织专业专职智能体并发作业；\n"
                "3. 保持全流程数据准确与实事求是原则。"
            ),
            tools=[],
            model_tier="fast",
        )

        # 2. 全网市场情报分析专员 (web_agent / web)
        self.define_subagent(
            name="web_agent",
            role="全网市场情报分析专员",
            description="负责全网网页检索电商商品的最新行业趋势、社媒痛点、主流品牌卖点与消费者评价。",
            system_prompt=(
                "你是资深电商市场研究员。你的任务是针对指定商品，利用全网网页搜索提炼：\n"
                "1. 主流品牌与热销款式；\n"
                "2. 消费者真实痛点与高频差评点；\n"
                "3. 当前全网网页零售与社媒讨论热点。\n"
                "输出必须严谨客观，基于搜索事实，禁止编造虚假数据。"
            ),
            tools=[search_web],
            model_tier="fast",
        )

        # 3. 货源比价专员 (scanner_agent / scanner)
        self.define_subagent(
            name="scanner_agent",
            role="货源比价专员",
            description="专门深入 1688 批发平台与海外亚马逊平台抓取真实出厂价格、工厂销量与海外亚马逊零售价，测算跨境与国内套利毛利。",
            system_prompt=(
                "你是资深电商供应链采购专家。你的任务是深入 1688 批发货源与海外亚马逊跨境渠道：\n"
                "1. 调研 1688 一手工厂批发底价、起批量与主流供货产地；\n"
                "2. 探查海外亚马逊终端竞品零售价格与热销形态；\n"
                "3. 交叉比对两端价差，测算跨境出海与国内套利毛利空间。\n"
                "输出清晰的数据分析要点，严禁虚构价格。"
            ),
            tools=[search_1688_wholesale, search_amazon],
            model_tier="fast",
        )

        # 4. 商品真实性验真专员 (verifier_agent / verifier)
        self.define_subagent(
            name="verifier_agent",
            role="商品真实性验真专员",
            description="交叉比对零售宣称卖点与供应链实际出厂参数，剔除夸大与虚假营销信息。",
            system_prompt=(
                "你是电商合规与产品质检专家。你的任务是交叉比对全网宣称卖点与工厂真实工艺参数：\n"
                "1. 鉴别“伪创新”与夸大宣传；\n"
                "2. 核算零售价与出厂成本之间的真实毛利空间与溢价来源；\n"
                "3. 标出值得信任的可靠数据源。"
            ),
            tools=[],
            model_tier="fast",
        )

        # 5. 电商市场调研研报专家 (reporter_agent / reporter)
        self.define_subagent(
            name="reporter_agent",
            role="电商市场调研研报专家",
            description="聚合各专职智能体的结构化发现与工件，撰写专业严谨的 Markdown 调研报告。",
            system_prompt=(
                "你是资深电商业务操盘负责人。你的任务是聚合前方各子智能体的调查成果：\n"
                "1. 梳理完整的市场概览与竞争格局；\n"
                "2. 给出供应链批发底价到零售终端的价格带图谱；\n"
                "3. 提炼核心产品卖点与差异化破局建议；\n"
                "4. 产出符合行业规范的高品质 Markdown 调研报告。"
            ),
            tools=[],
            model_tier="strong",
        )


default_registry = SubagentRegistry()


def get_default_registry() -> SubagentRegistry:
    """获取默认子智能体注册表。"""
    return default_registry


def set_default_registry(reg: SubagentRegistry) -> None:
    """设置默认子智能体注册表。"""
    global default_registry
    default_registry = reg
