"""MCP Server 工具接口（对齐 Spring AI 1.1 MCP Server 的 JSON-RPC 风格）。

Java 侧（Spring Boot 3.4 + Spring AI 1.1）作为工具提供方，
Python 侧 Agent 作为工具消费方；此处提供 Python 侧等价实现，便于双语言架构联调。
工具围绕电商商品知识域：商品参数查询 / 型号对比 / 配件兼容适配 / 使用教程 / 售后 FAQ，
查询逻辑对准商品图谱（BRAND/CATEGORY/PRODUCT/SKU/ACCESSORY/ISSUE）与四路召回检索引擎。
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...graph import get_graph_store
from ...ingestion.guard import assert_tool_allowed
from ...retrieval import get_retrieval_engine
from ...schemas.common import PermissionContext
from ...security.auth import User, current_user, require_permission, identity

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    user_context: Dict[str, Any] = Field(default_factory=dict)


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


TOOLS: List[ToolSchema] = [
    ToolSchema(name="query_product_spec",
               description="查询商品参数（切幅/防窥角度/喷头/打印精度/磁吸/防摔等）",
               parameters={"product_name": "string，商品型号，如 膜切机MC-500",
                           "spec_keys": "list<string> 可选，筛选参数名"}),
    ToolSchema(name="query_sku_diff",
               description="两个商品型号之间的差异对比（参数/配置/定位/价格带）",
               parameters={"product_a": "string，型号 A", "product_b": "string，型号 B"}),
    ToolSchema(name="query_compatibility",
               description="查询机型适配：哪些膜/壳支持某机型、某膜支持哪些机型（COMPATIBLE_WITH 关系，双向）",
               parameters={"accessory": "string 可选，膜/壳/配件型号如 C3、T20",
                           "product": "string 可选，商品/机型如 膜切机MC-500"}),
    ToolSchema(name="query_tutorial",
               description="查询商品使用教程（上手/贴膜/校准/维护保养）",
               parameters={"product": "string 可选，商品型号", "topic": "string，教程主题"}),
    ToolSchema(name="query_faq",
               description="查询售后 FAQ 与保修政策（质保/7天无理由/以旧换新/故障处理）",
               parameters={"question": "string，售后问题"}),
    ToolSchema(name="search_knowledge",
               description="四路召回（Embedding+BM25+HyDE+RRF）检索商品知识库",
               parameters={"query": "string", "top_k": "int"}),
]


def _permission(payload: ToolCallRequest, user: User) -> PermissionContext:
    ctx = payload.user_context or {}
    uid, org = identity(user, str(ctx.get("user_id", "")), str(ctx.get("org_tag", "")))
    return PermissionContext(
        user_id=uid,
        org_tag=org,
        roles=user.roles,
        is_admin=user.is_admin,
    )


@router.get("/tools")
async def list_tools(user: User = Depends(require_permission("knowledge:read"))) -> Dict[str, Any]:
    return {"tools": [t.model_dump() for t in TOOLS]}


@router.post("/tools")
def call_tool(payload: ToolCallRequest,
                    user: User = Depends(require_permission("knowledge:read"))) -> Dict[str, Any]:
    try:
        assert_tool_allowed(_tool_category(payload.tool_name))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    handler = _HANDLERS.get(payload.tool_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"未知工具：{payload.tool_name}")
    if payload.tool_name in ("query_product_spec", "query_sku_diff", "query_compatibility") and not user.has("admin:all"):
        raise HTTPException(status_code=403, detail="未隔离的全局图谱仅管理员可用；请使用 search_knowledge")
    result = handler(payload.arguments or {}, _permission(payload, user))
    return {"tool_name": payload.tool_name, "result": result}


def _tool_category(tool_name: str) -> str:
    return {
        "query_product_spec": "vector_search",
        "query_sku_diff": "vector_search",
        "query_compatibility": "graph_query",
        "query_tutorial": "search_corpus",
        "query_faq": "search_corpus",
        "search_knowledge": "vector_search",
    }.get(tool_name, "unknown")


# ───────────────────────── 工具实现 ─────────────────────────


def _edge_types(*names: str) -> List[Any]:
    """按成员名解析边类型（对图谱 schema 演进保持容错）。"""
    from ...schemas.graph import EdgeType

    return [EdgeType[n] for n in names if n in EdgeType.__members__]


def _hit_brief(h: Any) -> Dict[str, Any]:
    return {"rank": h.rank, "score": round(h.score, 4), "channel": h.channel,
            "doc_name": h.chunk.doc_name, "page": h.chunk.page,
            "text": h.chunk.text[:300]}


def _product_spec(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    name = str(args.get("product_name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="product_name 必填")
    store = get_graph_store()
    node = store.get_node(name)
    if node is None:
        candidates = store.search(name, limit=5)
        return {"found": False, "product_name": name,
                "candidates": [n.name for n in candidates]}
    spec_keys = [str(k) for k in (args.get("spec_keys") or [])]
    props = dict(node.props)
    if spec_keys:
        props = {k: v for k, v in props.items()
                 if any(s in k or k in s for s in spec_keys)}
    hits = get_retrieval_engine().retrieve(
        f"{name} 商品参数 规格", top_k=5, permission=permission)
    return {
        "found": True,
        "product_name": name,
        "type": node.type.value,
        "specs": props,
        "skus": [{"source": r.get("source"), "target": r.get("target"),
                  "relation": r.get("relation")}
                 for r in store.neighbors(name, _edge_types("HAS_SKU"), "both", 1)],
        "references": [_hit_brief(h) for h in hits],
    }


def _sku_diff(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    name_a = str(args.get("product_a", "")).strip()
    name_b = str(args.get("product_b", "")).strip()
    if not name_a or not name_b:
        raise HTTPException(status_code=400, detail="product_a 与 product_b 必填")
    store = get_graph_store()
    node_a = store.get_node(name_a)
    node_b = store.get_node(name_b)
    hits = get_retrieval_engine().retrieve(
        f"{name_a} {name_b} 区别 差异 对比", top_k=6, permission=permission)
    return {
        "product_a": {"name": name_a, "found": node_a is not None,
                      "props": dict(node_a.props) if node_a else {}},
        "product_b": {"name": name_b, "found": node_b is not None,
                      "props": dict(node_b.props) if node_b else {}},
        "references": [_hit_brief(h) for h in hits],
    }


def _compatibility(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    accessory = str(args.get("accessory", "")).strip()
    product = str(args.get("product", "")).strip()
    if not accessory and not product:
        raise HTTPException(status_code=400, detail="accessory 与 product 至少填一个")
    store = get_graph_store()
    target = accessory or product
    node = store.get_node(target)
    relations = store.neighbors(target, _edge_types("COMPATIBLE_WITH"), "both", 1) if node else []
    query = " ".join(x for x in (accessory, product, "适配 兼容") if x)
    hits = get_retrieval_engine().retrieve(query, top_k=6, permission=permission)
    return {
        "target": target,
        "found": node is not None,
        "compatible_with": [
            {"relation": r.get("relation"), "source": r.get("source"),
             "target": r.get("target")}
            for r in relations
        ],
        "references": [_hit_brief(h) for h in hits],
    }


def _tutorial(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    product = str(args.get("product", "")).strip()
    topic = str(args.get("topic", "")).strip()
    if not topic and not product:
        raise HTTPException(status_code=400, detail="topic 必填")
    query = " ".join(x for x in (product, topic, "使用教程") if x)
    hits = get_retrieval_engine().retrieve(query, top_k=6, permission=permission)
    return {
        "product": product,
        "topic": topic,
        "tutorials": [_hit_brief(h) for h in hits],
    }


def _faq(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    question = str(args.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 必填")
    hits = get_retrieval_engine().retrieve(
        f"{question} 售后 保修 政策", top_k=6, permission=permission)
    return {
        "question": question,
        "answers": [_hit_brief(h) for h in hits],
    }


def _search_knowledge(args: Dict[str, Any], permission: PermissionContext) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    try:
        top_k = int(args.get("top_k",8))
        if not 1 <= top_k <= 30: raise ValueError()
    except (ValueError,TypeError):
        raise HTTPException(status_code=400,detail="top_k 必须是 1-30 的整数")
    hits = get_retrieval_engine().retrieve(query, top_k=top_k, permission=permission)
    return {
        "query": query,
        "hits": [_hit_brief(h) for h in hits],
    }


_HANDLERS = {
    "query_product_spec": _product_spec,
    "query_sku_diff": _sku_diff,
    "query_compatibility": _compatibility,
    "query_tutorial": _tutorial,
    "query_faq": _faq,
    "search_knowledge": _search_knowledge,
}
