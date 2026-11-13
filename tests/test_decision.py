"""编排层决策与意图路由引擎单元测试。"""
import os
from pathlib import Path
from dotenv import dotenv_values
import pytest
from taozhi.orchestration.decision import (
    DecisionEngine,
    DecisionPath,
    VerificationResult,
    get_decision_engine,
)

# 仅在非 test 离线环境下允许从 .env 读取外部凭证
_is_test_env = os.environ.get("ECOM_MODE") == "test" or os.environ.get("ECOM_ENV_FILE", "").endswith(".env.test")
_env_path = Path(__file__).resolve().parents[1] / ".env"
if not _is_test_env and _env_path.exists():
    _cfg = dotenv_values(_env_path)
    if _cfg.get("API_KEY"):
        os.environ["API_KEY"] = _cfg["API_KEY"]
    if _cfg.get("BASE_URL"):
        os.environ["BASE_URL"] = _cfg["BASE_URL"]
    from taozhi.config import get_settings
    get_settings.cache_clear()

SPECIALIST_OPTIONS = {
    "product": "商品规格与参数：核对型号、尺寸、厚度、材质、孔位、物理与工艺参数，不混用不同型号",
    "compatibility": "产品适配与兼容：核对手机机型、屏幕形态（曲面屏/折叠屏/直板）、膜壳兼容、贴合度与互斥替换",
    "after_sales": "客服售后与质保：核对保修质保期、开胶开裂换新政策、退换货凭证、使用故障排查与客服处理",
    "operations": "运营选品与卖点：核对商品卖点文案、选品依据、转化理由与营销策略",
    "supply": "库存供应链与物流：核对采购周期、库存快照、发货时效与仓储物流",
    "finance": "财务与经营分析：核对财务依据、采购成本、利润核算与资金情况",
}


def test_decision_choice_semantic_fast_path():
    """测试快速语义向量近邻离散决策。"""
    from taozhi.knowledge.embedding import available as embedding_available
    if not embedding_available():
        pytest.skip("本地向量嵌入服务未运行，跳过语义快道测试")
    engine = get_decision_engine()

    # 预热选项向量缓存（纯本地向量化，禁止调用外部 LLM）
    engine.choice("预热初始化", SPECIALIST_OPTIONS, multi_select=False, allow_llm=False)

    # 明确的售后退换问题
    res = engine.choice("用了一周边缘起泡开胶了，给换新吗？", SPECIALIST_OPTIONS, multi_select=False)
    assert res.selected == ["after_sales"]
    assert res.confidence >= 0.4
    assert res.path == DecisionPath.FAST_SEMANTIC
    assert res.latency_ms < 1000.0
    assert "after_sales" in res.distribution


@pytest.mark.slow
def test_decision_choice_llm_multi_select():
    """测试多角色协同规划模型结构化决策。"""
    from taozhi.config import get_settings
    if not get_settings().has_llm:
        pytest.skip("未配置真实 LLM API 凭据，跳过 LLM 结构化多选决策测试")
    engine = get_decision_engine()

    context = "这款钢化膜厚度是多少？贴了之后会不会顶华为Mate60的素皮手机壳？"
    res = engine.choice(context, SPECIALIST_OPTIONS, multi_select=True, top_k=2)
    if res.path == DecisionPath.HEURISTIC_FALLBACK:
        pytest.skip("上游 LLM 触发 TPM/RPM 速率限制降级，跳过 LLM 选择决策断言")
    assert len(res.selected) <= 2
    assert any(r in res.selected for r in ["product", "compatibility"])
    assert res.path == DecisionPath.FAST_LLM
    assert res.confidence > 0.0


@pytest.mark.slow
def test_decision_score_primitive():
    """测试连续打分评估原语。"""
    from taozhi.config import get_settings
    if not get_settings().has_llm:
        pytest.skip("未配置真实 LLM API 凭据，跳过 LLM 打分原语测试")
    engine = get_decision_engine()

    criterion = "根据回答是否严谨陈述了保修换新条款进行打分，1.0为完全严谨，0.0为完全未提及"
    content = "根据售后政策，凡在自营渠道购买并在7天内出现非人为起泡开胶的，可凭订单截图申请免费包邮换新一次。"

    res = engine.score(content, criterion, scale=(0.0, 1.0))
    if res.path == DecisionPath.HEURISTIC_FALLBACK:
        pytest.skip("上游 LLM 触发 TPM/RPM 速率限制降级，跳过打分断言")
    assert 0.7 <= res.score <= 1.0
    assert res.confidence >= 0.5
    assert len(res.rationale) > 0


@pytest.mark.slow
def test_decision_verify_primitive():
    """测试布尔事实断言核验原语（Verify）。"""
    from taozhi.config import get_settings
    if not get_settings().has_llm:
        pytest.skip("未配置真实 LLM API 凭据，跳过 LLM 断言核验测试")
    engine = get_decision_engine()

    context = "本商品为直屏专用钢化膜，不支持任何曲面屏或折叠屏机型。"

    # 核验断言 1: 是否支持折叠屏 (应为 False)
    res_false = engine.verify(context, "该钢化膜可用于华为折叠屏手机")
    if res_false.path == DecisionPath.HEURISTIC_FALLBACK:
        pytest.skip("上游 LLM 触发 TPM/RPM 速率限制降级，跳过布尔断言")
    assert res_false.value is False
    assert res_false.confidence >= 0.7

    # 核验断言 2: 是否直屏专用 (应为 True)
    res_true = engine.verify(context, "该产品适用于直屏手机")
    if res_true.path == DecisionPath.HEURISTIC_FALLBACK:
        pytest.skip("上游 LLM 触发 TPM/RPM 速率限制降级，跳过布尔断言")
    assert res_true.value is True
    assert res_true.confidence >= 0.7


def test_decision_offline_safe_fallback():
    """测试在完全离线无外部模型凭据与无向量服务环境下，决策原语安全降级保障。"""
    engine = get_decision_engine()
    # Choice 离线降级
    res = engine.choice("用了一周边缘起泡开胶了，给换新吗？", SPECIALIST_OPTIONS, allow_llm=False)
    assert res.selected
    assert res.path in (DecisionPath.FAST_SEMANTIC, DecisionPath.HEURISTIC_FALLBACK)

    # Score 安全降级/执行
    score_res = engine.score("测试内容", "评分标准")
    assert 0.0 <= score_res.score <= 1.0
    assert score_res.path in (DecisionPath.FAST_LLM, DecisionPath.HEURISTIC_FALLBACK)

    # Verify 安全降级/执行
    verify_res = engine.verify("直屏专用", "适用于折叠屏")
    assert isinstance(verify_res.value, bool)
    assert verify_res.path in (DecisionPath.FAST_LLM, DecisionPath.HEURISTIC_FALLBACK)