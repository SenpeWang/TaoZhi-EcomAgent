"""子智能体类型定义与全局注册表（Subagent Registry）。

遵循 Antigravity (agy) 设计规范：
1. define_subagent 动态注册机制；
2. 预置全网调研、货源挖掘、交叉验真、综合报告等核心智能体类型；
3. 工具集合与模型梯度按角色严格绑定。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .protocol import SubagentDefinition
from .tools import fetch_web_page, search_1688_wholesale, search_web


class SubagentRegistry:
    """子智能体类型注册表，管理系统所有可用角色模板。"""

    def __init__(self) -> None:
        """初始化注册表并装载内置角色。"""
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
        """动态注册一个新的子智能体类型（对照 agy 的 define_subagent）。

        Args:
            name: 类型唯一英文字符串标识。
            role: 2-5 字中文岗位名称。
            description: 智能体应用场景与目标说明。
            system_prompt: 专门定制的 System 指令。
            tools: 允许该智能体调用的工具函数列表。
            model_tier: 指定模型等级 (fast / strong / vision)。
            enable_write: 是否开启写入工件能力。
            enable_subagent: 是否允许继续派生下级。

        Returns:
            SubagentDefinition: 注册成功的定义对象。
        """
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
        """获取已注册的智能体定义。

        Args:
            name: 智能体类型名称。

        Returns:
            Optional[SubagentDefinition]: 定义对象；未找到返回 None。
        """
        return self._definitions.get(name)

    def list_all(self) -> List[SubagentDefinition]:
        """列出当前所有已注册的智能体类型。

        Returns:
            List[SubagentDefinition]: 定义对象列表。
        """
        return list(self._definitions.values())

    def _register_builtins(self) -> None:
        """注册内置的电商调研子智能体家族。"""
        # 1. 全网趋势与竞品搜索智能体
        self.define_subagent(
            name="web_researcher",
            role="全网市场情报分析专员",
            description="负责全网搜索电商商品的最新行业趋势、社媒痛点、主流品牌卖点与用户舆论。",
            system_prompt=(
                "你是资深电商市场研究员。你的任务是针对指定商品，利用网络搜索提炼：\n"
                "1. 主流品牌与热销款式；\n"
                "2. 消费者真实痛点与高频差评点；\n"
                "3. 当前全网零售价格区间分布。\n"
                "输出必须严谨客观，基于搜索事实，禁止编造虚假数据。"
            ),
            tools=[search_web, fetch_web_page],
            model_tier="fast",
        )

        # 2. 1688 批发货源与工厂成本挖掘智能体
        self.define_subagent(
            name="ecommerce_scanner",
            role="1688货源比价专员",
            description="专门深入 1688 批发平台抓取真实出厂价格、工厂销量、起批量及供应商分布。",
            system_prompt=(
                "你是资深电商供应链采购专家。你的任务是深入 1688 批发货源渠道：\n"
                "1. 调研一手工厂批发底价及梯度价格；\n"
                "2. 统计月成交额与工厂回头率；\n"
                "3. 识别主流供货产地（如深圳、东莞、义乌等）。\n"
                "输出清晰的数据分析要点，严禁虚构价格。"
            ),
            tools=[search_1688_wholesale],
            model_tier="fast",
        )

        # 3. 真实性与虚假宣传交叉验真智能体
        self.define_subagent(
            name="data_verifier",
            role="商品真实性验真专员",
            description="交叉比对零售宣称卖点与供应链实际出厂参数，剔除夸大与虚假营销信息。",
            system_prompt=(
                "你是电商合规与产品质检专家。你的任务是交叉比对全网宣称卖点与工厂真实工艺参数：\n"
                "1. 鉴别“伪创新”与夸大宣传（如把普通硅胶宣传成航天科技）；\n"
                "2. 核算零售价与出厂成本之间的真实毛利空间与溢价来源；\n"
                "3. 标出值得信任的可靠数据源。"
            ),
            tools=[],
            model_tier="fast",
        )

        # 4. 终审汇总与研报撰写智能体
        self.define_subagent(
            name="synthesis_reporter",
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


# 单例注册表实例
default_registry = SubagentRegistry()
