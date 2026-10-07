"""电商商品知识问答工作流冒烟测试（端到端用例需要 LLM，默认跳过）。

运行端到端：RUN_SLOW_TESTS=1 pytest tests/test_workflow_smoke.py -m slow -s
"""

import os

import pytest

from ecom_copilot.agents.supervisor import _rule_intent, decide_route
from ecom_copilot.agents.state import initial_state
from ecom_copilot.schemas.research import ResearchDepth, ResearchTask


def test_intent_rules():
    assert _rule_intent("MC-500 膜切机的切幅参数是多少") == "spec_query"
    assert _rule_intent("MC-500 和 MC-300 有什么区别") == "sku_compare"
    assert _rule_intent("C5 陶瓷膜能不能用在 Mate X5 上") == "compat_recommend"
    assert _rule_intent("UV 打印机堵头了怎么办") == "after_sales"


def test_route_decision_order():
    task = ResearchTask(question="测试", depth=ResearchDepth.QUICK)
    state = initial_state(task)
    assert decide_route(state) == "data_collection"
    state["documents"] = [object()]  # type: ignore[index]
    assert decide_route(state) == "doc_parsing"
    state["chunks"] = [object()]  # type: ignore[index]
    assert decide_route(state) == "knowledge_extraction"


@pytest.mark.slow
@pytest.mark.skipif(
    os.getenv("RUN_SLOW_TESTS", "0") != "1",
    reason="端到端测试需要调用 LLM，设置 RUN_SLOW_TESTS=1 后运行",
)
def test_end_to_end_quick():
    from ecom_copilot.agents.workflow import get_workflow
    from ecom_copilot.schemas.common import PermissionContext

    task = ResearchTask(
        question="膜法工坊 MC-500 和 MC-300 有什么区别",
        category="膜切机",
        product_line="MC系列",
        depth=ResearchDepth.QUICK,
    )
    state = get_workflow().run(task, PermissionContext(user_id="test", org_tag="test"))
    assert state.get("documents")
    assert state.get("graph_result")
    report = state.get("report")
    assert report is not None
    assert report.citation_coverage >= 0.5
