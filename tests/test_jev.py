"""Jev System 1 决策引擎单元测试与基准验证。"""
import os
from pathlib import Path
from dotenv import dotenv_values
import pytest
from ecom_copilot.enterprise.jev import get_jev_engine, DecisionPath

# 确保在 pytest 运行环境下正确继承 .env 中的 LLM 凭证
_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    _cfg = dotenv_values(_env_path)
    if _cfg.get("API_KEY"):
        os.environ["API_KEY"] = _cfg["API_KEY"]
    if _cfg.get("BASE_URL"):
        os.environ["BASE_URL"] = _cfg["BASE_URL"]
    from ecom_copilot.config import get_settings
    get_settings.cache_clear()

SPECIALIST_OPTIONS = {
    "product": "商品规格与参数：核对型号、尺寸、厚度、材质、孔位、物理与工艺参数，不混用不同型号",
    "compatibility": "产品适配与兼容：核对手机机型、屏幕形态（曲面屏/折叠屏/直板）、膜壳兼容、贴合度与互斥替换",
    "after_sales": "客服售后与质保：核对保修质保期、开胶开裂换新政策、退换货凭证、使用故障排查与客服处理",
    "operations": "运营选品与卖点：核对商品卖点文案、选品依据、转化理由与营销策略",
    "supply": "库存供应链与物流：核对采购周期、库存快照、发货时效与仓储物流",
    "finance": "财务与经营分析：核对财务依据、采购成本、利润核算与资金情况",
}


def test_jev_choice_semantic_fast_path():
    """测试 Jev 快速语义近邻离散决策（System 1a 高置信度直达）。"""
    jev = get_jev_engine()

    # 预热选项向量缓存
    jev.choice("预热初始化", SPECIALIST_OPTIONS, multi_select=False)

    # 明确的售后退换问题
    res = jev.choice("用了一周边缘起泡开胶了，给换新吗？", SPECIALIST_OPTIONS, multi_select=False)
    assert res.selected == ["after_sales"]
    assert res.confidence >= 0.4
    assert res.path == DecisionPath.FAST_SEMANTIC
    assert res.latency_ms < 100.0  # 缓存预热后应在 50ms 内
    assert "after_sales" in res.distribution


def test_jev_choice_llm_multi_select():
    """测试 Jev 多角色协同规划决策（System 1b 结构化 LLM 决策）。"""
    jev = get_jev_engine()

    # 既问尺寸参数又问兼容手机壳，属于规格与兼容双角色
    context = "这款钢化膜厚度是多少？贴了之后会不会顶华为Mate60的素皮手机壳？"
    res = jev.choice(context, SPECIALIST_OPTIONS, multi_select=True, top_k=2)
    assert len(res.selected) <= 2
    assert any(r in res.selected for r in ["product", "compatibility"])
    assert res.path == DecisionPath.FAST_LLM
    assert res.confidence > 0.0


def test_jev_score_primitive():
    """测试 Jev Score 连续打分评估原语。"""
    jev = get_jev_engine()

    criterion = "根据回答是否严谨陈述了保修换新条款进行打分，1.0为完全严谨，0.0为完全未提及"
    content = "根据售后政策，凡在自营渠道购买并在7天内出现非人为起泡开胶的，可凭订单截图申请免费包邮换新一次。"

    res = jev.score(content, criterion, scale=(0.0, 1.0))
    assert 0.7 <= res.score <= 1.0
    assert res.confidence >= 0.5
    assert len(res.rationale) > 0


def test_jev_noul_primitive():
    """测试 Jev Noul 布尔断言核验原语。"""
    jev = get_jev_engine()

    context = "本商品为直屏专用钢化膜，不支持任何曲面屏或折叠屏机型。"

    # 核验断言 1: 是否支持折叠屏 (应为 False)
    res_false = jev.noul(context, "该钢化膜可用于华为折叠屏手机")
    assert res_false.value is False
    assert res_false.confidence >= 0.7

    # 核验断言 2: 是否直屏专用 (应为 True)
    res_true = jev.noul(context, "该产品适用于直屏手机")
    assert res_true.value is True
    assert res_true.confidence >= 0.7
