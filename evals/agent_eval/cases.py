"""调研评测目录，包含实际调研任务与固定证据的合成回放任务。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

AGENT_LIVE_CASES: List[Dict[str, Any]] = [
    {
        "id": "AGENT-STD-001",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "full_research",
        "name": "标准全景调研协同测试（正常流程）",
        "prompt": "对【智能保温杯】进行全景电商市场调研，覆盖消费者痛点、一手出厂底价与海外对标。",
        "keyword": "智能保温杯",
        "required_tools": ["search_web", "search_1688_wholesale", "search_amazon"],
        "forbidden_tools": [],
        "required_sections": ["市场", "1688", "建议"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-SCAN-002",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "sourcing_comparison",
        "name": "供应链一手出厂价与海外零售对标测试（复杂多步）",
        "prompt": "深入 1688 批发市场与海外亚马逊平台，对比【磁吸手机壳】的一手出厂底价与美元零售价，测算套利毛利。",
        "keyword": "磁吸手机壳",
        "required_tools": ["search_1688_wholesale", "search_amazon"],
        "forbidden_tools": [],
        "required_sections": ["出厂", "零售", "毛利"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-WEB-003",
        "capability": "tool_use",
        "category": "tool_correctness",
        "track": "capability",
        "topic": "web_intelligence",
        "name": "纯全网网页检索与评价提取边界测试（边界隔离防越权）",
        "prompt": "检索全网关于【骨传导运动耳机】的最新行业趋势、主流热销卖点与买家高频差评点。",
        "keyword": "骨传导运动耳机",
        "required_tools": ["search_web"],
        "forbidden_tools": ["search_amazon", "search_1688_wholesale"],
        "required_sections": ["全网", "卖点", "痛点"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-STEER-004",
        "capability": "planning_and_adaptation",
        "category": "mid_trajectory_steering",
        "track": "capability",
        "topic": "dynamic_steering",
        "name": "运行期动态干预与实时重规划测试（复杂多步）",
        "prompt": "调研【大容量充电宝】的供应链货源。",
        "keyword": "大容量充电宝",
        "steering": ["优先对比义乌源头工厂报价，重点排查电芯虚标风险"],
        "steering_keyword": "义乌",
        "required_tools": ["search_1688_wholesale"],
        "forbidden_tools": [],
        "required_sections": ["供应链", "充电宝"],
        "min_chars": 60,
    },
    {
        "id": "AGENT-ERR-005",
        "capability": "planning_and_adaptation",
        "category": "error_recovery",
        "track": "capability",
        "topic": "error_recovery",
        "name": "冷门品类与数据源降级容错测试（异常容错自愈）",
        "prompt": "对【极冷门高精密特种陶瓷轴承】进行调研，评估在源头工厂货源极稀缺情况下的降级分析与风险研报能力。",
        "keyword": "极冷门高精密特种陶瓷轴承",
        "required_tools": ["search_web"],
        "forbidden_tools": [],
        "required_sections": ["市场", "建议"],
        "min_chars": 60,
    },
    {
        "id": "AGENT-CAT-006",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "full_research",
        "name": "桌面静音加湿器全景市场调研（数码小家电品类）",
        "prompt": "调研【桌面静音加湿器】的全网消费者痛点与 1688 源头工厂出厂价与亚马逊售价。",
        "keyword": "桌面静音加湿器",
        "required_tools": ["search_web", "search_1688_wholesale", "search_amazon"],
        "forbidden_tools": [],
        "required_sections": ["市场", "出厂", "建议"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-CAT-007",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "sourcing_comparison",
        "name": "户外轻量冲锋衣一手货源与跨境零售对标（服饰户外品类）",
        "prompt": "深入 1688 批发市场探寻【户外轻量冲锋衣】的出厂底价，并与海外亚马逊价格进行毛利对比测算。",
        "keyword": "户外轻量冲锋衣",
        "required_tools": ["search_1688_wholesale", "search_amazon"],
        "forbidden_tools": [],
        "required_sections": ["出厂", "零售", "建议"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-CAT-008",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "sourcing_comparison",
        "name": "氨基酸温和洁面乳源头工厂起订量与配方成本核验（美妆个护品类）",
        "prompt": "调取 1688 批发货源探查【氨基酸温和洁面乳】的一手代工出厂价与起订批量梯度。",
        "keyword": "氨基酸温和洁面乳",
        "required_tools": ["search_1688_wholesale"],
        "forbidden_tools": [],
        "required_sections": ["出厂", "建议"],
        "min_chars": 60,
    },
    {
        "id": "AGENT-CAT-009",
        "capability": "task_completion",
        "category": "task_completion",
        "track": "capability",
        "topic": "full_research",
        "name": "智能自动猫砂盆跨境高客单价溢价分析（宠物智能硬件品类）",
        "prompt": "对【智能自动猫砂盆】开展全景市场调研，比对国内 1688 出厂价与海外亚马逊高溢价零售空间。",
        "keyword": "智能自动猫砂盆",
        "required_tools": ["search_web", "search_1688_wholesale", "search_amazon"],
        "forbidden_tools": [],
        "required_sections": ["市场", "1688", "建议"],
        "min_chars": 80,
    },
    {
        "id": "AGENT-SEC-010",
        "capability": "tool_use",
        "category": "tool_correctness",
        "track": "capability",
        "topic": "web_intelligence",
        "name": "纯全网用户真实口碑与差评分析防越权测试（边界隔离防越权）",
        "prompt": "仅检索全网关于【人体工学办公椅】的用户真实口碑、高频腰部支撑差评点与行业趋势。",
        "keyword": "人体工学办公椅",
        "required_tools": ["search_web"],
        "forbidden_tools": ["search_amazon", "search_1688_wholesale"],
        "required_sections": ["市场", "卖点", "痛点"],
        "min_chars": 80,
    },
]


def _load_synthetic_cases() -> list[dict]:
    base = Path(__file__).resolve().parents[1] / "datasets"
    candidates = sorted(base.glob("agent_v*/research_cases.json"), key=lambda item: item.parent.name)
    if not candidates:
        raise ValueError("未找到可用的调研合成用例集")
    path = candidates[-1]
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1 or catalog.get("source") != "synthetic":
        raise ValueError("无法识别调研回放用例集版本或来源")
    cases = catalog["cases"]
    if not cases or any(case.get("execution_mode") != "synthetic_trace_regrade" for case in cases):
        raise ValueError("合成调研用例须声明回放执行方式")
    return cases


AGENT_BENCHMARK_CASES: List[Dict[str, Any]] = list(AGENT_LIVE_CASES) + _load_synthetic_cases()
