"""商品适配图谱 API：Cypher 查询 / 可视化 / 邻居钻取 / 适配覆盖缺口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...graph import get_graph_store, run_cypher
from ...graph.builder import IndustryGraphBuilder
from ...schemas.common import PermissionContext
from ...schemas.graph import EdgeType, PriceBand
from ...security.auth import User, current_user, require_permission

router = APIRouter(prefix="/api/graph", tags=["graph"])


class GraphQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cypher: str
    include_evidence: bool = True
    user_context: Dict[str, Any] = Field(default_factory=dict)


@router.post("/query")
async def query_graph(payload: GraphQueryRequest,
                      user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    store = get_graph_store()
    try:
        result = run_cypher(payload.cypher, store, include_evidence=payload.include_evidence)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Cypher 解析失败：{exc}") from exc
    return result.model_dump(mode="json")


@router.get("/vis")
async def graph_visualization(limit: int = 300, user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    store = get_graph_store()
    if hasattr(store, "to_vis_payload"):
        return store.to_vis_payload(limit)  # type: ignore[attr-defined]
    return {"nodes": [], "edges": []}


@router.get("/stats")
async def graph_stats(user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    return get_graph_store().stats().model_dump(mode="json")


@router.get("/neighbors/{name}")
async def neighbors(name: str, depth: int = 1, direction: str = "both",
                    relation: Optional[str] = None,
                    user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    edge_types = None
    if relation:
        try:
            edge_types = [EdgeType(relation)]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"未知关系类型：{relation}") from exc
    items = get_graph_store().neighbors(name, edge_types, direction, depth)
    return {"name": name, "depth": depth, "relations": items}


@router.get("/band/{band}")
async def nodes_by_band(band: str, limit: int = 100,
                        user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    try:
        price_band = PriceBand(band)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"未知价位段：{band}") from exc
    nodes = get_graph_store().nodes(price_band=price_band)[:limit]
    return {"band": band, "products": [n.name for n in nodes]}


@router.get("/gap")
async def gap_analysis(category: str = "", user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    builder = IndustryGraphBuilder()
    return builder.gap_analysis(category=category)


@router.get("/cross/{product}")
async def cross_reasoning(product: str, user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    """跨图谱推理：商品的兼容配件有哪些、可替代/被替代型号是什么（兼容/替代推理）。"""
    builder = IndustryGraphBuilder()
    return {
        "product": product,
        "compatible_accessories": builder.compatible_accessories(product),
        "supersede_chain": builder.supersede_chain(product),
    }
