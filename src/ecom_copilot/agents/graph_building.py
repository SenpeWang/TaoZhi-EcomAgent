"""④ 适配图谱 Agent：商品适配图谱构建 + 动态更新 + 适配覆盖缺口识别。"""

from __future__ import annotations

import time
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from ..observability import inc
from ..schemas.graph import PriceBand
from .state import ResearchRuntime, ResearchState, append_trace


def graph_building_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    task = state["task"]
    started = time.perf_counter()

    extraction = state.get("extraction")
    builder = runtime.graph_builder
    result: Dict[str, Any] = {"new_nodes": 0, "new_edges": 0, "updated_edges": 0}
    if builder is not None and extraction is not None:
        result = builder.build(extraction)

    payload: Dict[str, Any] = dict(result) if result else {}
    if builder is not None:
        try:
            payload["vis"] = builder.vis_payload(limit=200)
            payload["stats"] = builder.stats().model_dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
        # 适配覆盖缺口识别：按商品类目检查配件覆盖情况
        if task.category or (state.get("plan") and state.get("plan").intent == "compat_recommend"):
            try:
                payload["compatibility_gaps"] = builder.gap_analysis(category=task.category)
            except Exception:  # noqa: BLE001
                payload["compatibility_gaps"] = {}
        # 类目/价位段聚合：按价格档位（PriceBand）归组商品节点
        try:
            by_band: Dict[str, Any] = {}
            for node in builder.store.nodes():
                band = node.props.get("price_band", PriceBand.UNKNOWN.value)
                by_band.setdefault(band, []).append(node.name)
            payload["by_price_band"] = {k: v[:30] for k, v in by_band.items()}
        except Exception:  # noqa: BLE001
            payload["by_price_band"] = {}

    elapsed = time.perf_counter() - started
    inc("agent.graph_building")
    append_trace(state, "graph_building",
                 f"新增 {result.get('new_nodes', 0)} 节点 / {result.get('new_edges', 0)} 关系，"
                 f"更新 {result.get('updated_edges', 0)} 关系",
                 elapsed)

    return {
        "graph_result": payload,
        "trace": state.get("trace", []),
    }
