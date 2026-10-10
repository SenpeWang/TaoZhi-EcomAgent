"""MCP Server 工具接口（对齐 Spring AI 1.1 MCP Server 的 JSON-RPC 风格）。

Java 侧（Spring Boot 3.4 + Spring AI 1.1）作为工具提供方，
Python 侧 Agent 作为工具消费方；此处提供 Python 侧等价实现，便于双语言架构联调。
工具围绕电商商品知识域：商品参数查询 / 型号对比 / 配件兼容适配 / 使用教程 / 售后 FAQ，
查询逻辑对准商品图谱（BRAND/CATEGORY/PRODUCT/SKU/ACCESSORY/ISSUE）与四路召回检索引擎。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from threading import Lock
from typing import Any, Dict, List, Optional, Type

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


# ───────────────────── 工具入参强类型模型 (防参数幻觉 extra="forbid") ─────────────────────


class ProductSpecArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_name: str = Field(min_length=1, max_length=128, description="商品型号，如 膜切机MC-500")
    spec_keys: List[str] = Field(default_factory=list, max_length=32, description="可选筛选参数名")


class SkuDiffArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_a: str = Field(min_length=1, max_length=128, description="型号 A")
    product_b: str = Field(min_length=1, max_length=128, description="型号 B")


class CompatibilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    accessory: str = Field(default="", max_length=128, description="配件型号如 C3、T20")
    product: str = Field(default="", max_length=128, description="商品型号如 膜切机MC-500")

    @model_validator(mode="after")
    def validate_targets(self) -> CompatibilityArgs:
        if not self.accessory and not self.product:
            raise ValueError("accessory 与 product 至少填一个")
        return self


class TutorialArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    topic: str = Field(min_length=1, max_length=128, description="教程主题")
    product: str = Field(default="", max_length=128, description="可选商品型号")


class FaqArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=500, description="售后问题")


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500, description="检索词")
    top_k: int = Field(default=8, ge=1, le=30, description="返回数量（1-30）")


_TOOL_ARG_MODELS: Dict[str, Type[BaseModel]] = {
    "query_product_spec": ProductSpecArgs,
    "query_sku_diff": SkuDiffArgs,
    "query_compatibility": CompatibilityArgs,
    "query_tutorial": TutorialArgs,
    "query_faq": FaqArgs,
    "search_knowledge": SearchKnowledgeArgs,
}


# ───────────────────── 工具调用防死锁熔断器 (Tool Churn & Deadlock Breaker) ─────────────────────


class DeadlockDetectedError(RuntimeError):
    """检测到相同参数的工具连续重复调用，触发防死锁熔断。"""
    pass


class ToolDeadlockBreaker:
    """工具调用防死锁熔断器：检测同一调用者在短时间内对同一工具使用完全相同参数的连续震荡调用。"""

    def __init__(self, max_consecutive_duplicates: int = 3, window_seconds: float = 60.0) -> None:
        self.max_duplicates = max_consecutive_duplicates
        self.window_seconds = window_seconds
        self._history: Dict[str, deque] = {}  # caller_id -> deque of (fingerprint, timestamp)
        self._lock = Lock()

    @staticmethod
    def fingerprint(tool_name: str, arguments: Dict[str, Any]) -> str:
        """计算工具调用的确定性指纹 (Canonical SHA256)。"""
        raw = json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def check_and_record(self, caller_id: str, tool_name: str, arguments: Dict[str, Any]) -> None:
        fp = self.fingerprint(tool_name, arguments)
        now = time.time()
        with self._lock:
            if caller_id not in self._history:
                self._history[caller_id] = deque(maxlen=20)
            q = self._history[caller_id]

            consecutive_count = 0
            for prev_fp, prev_time in reversed(q):
                if now - prev_time > self.window_seconds:
                    break
                if prev_fp == fp:
                    consecutive_count += 1
                else:
                    break

            if consecutive_count >= self.max_duplicates:
                raise DeadlockDetectedError(
                    f"检测到工具 [{tool_name}] 连续 {consecutive_count + 1} 次使用完全相同的参数调用，"
                    f"已触发防死锁熔断保护。请调整参数策略或更换检索词。"
                )

            q.append((fp, now))

    def reset_caller(self, caller_id: str) -> None:
        with self._lock:
            self._history.pop(caller_id, None)


_DEADLOCK_BREAKER = ToolDeadlockBreaker()


# ───────────────────── 工具描述与负向语义约束 ─────────────────────


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


TOOLS: List[ToolSchema] = [
    ToolSchema(
        name="query_product_spec",
        description="精确查询已知具体型号的商品规格参数（切幅/防窥角度/喷头/打印精度等）。【负向约束】若用户仅有模糊意图或未提供明确型号，请勿使用本工具，改用 search_knowledge。",
        parameters={"product_name": "string，商品型号，如 膜切机MC-500",
                    "spec_keys": "list<string> 可选，筛选参数名"},
    ),
    ToolSchema(
        name="query_sku_diff",
        description="精确对比两个已知商品型号之间的参数与配置差异。【负向约束】若仅查询单品规格请用 query_product_spec；若涉及三个及以上商品或无明确型号对比，改用 search_knowledge。",
        parameters={"product_a": "string，型号 A", "product_b": "string，型号 B"},
    ),
    ToolSchema(
        name="query_compatibility",
        description="精确查询膜/壳/配件与机型的适配兼容关系（COMPATIBLE_WITH 关系，双向）。【负向约束】若属于故障排查、保养教程或政策问题，请勿使用本工具，改用 query_faq 或 query_tutorial。",
        parameters={"accessory": "string 可选，膜/壳/配件型号如 C3、T20",
                    "product": "string 可选，商品/机型如 膜切机MC-500"},
    ),
    ToolSchema(
        name="query_tutorial",
        description="查询商品官方使用教程与操作指引（上手/贴膜/校准/日常维护保养）。【负向约束】若属于售后退换、保修政策或硬件故障申报，请改用 query_faq。",
        parameters={"product": "string 可选，商品型号", "topic": "string，教程主题"},
    ),
    ToolSchema(
        name="query_faq",
        description="查询售后政策、保修退换规则与常见故障排查处置方案。【负向约束】若查询具体产品物理规格参数或操作指引，请改用 query_product_spec 或 query_tutorial。",
        parameters={"question": "string，售后问题"},
    ),
    ToolSchema(
        name="search_knowledge",
        description="四路召回（Embedding+BM25+HyDE+RRF）全库综合检索。【负向约束】若已有明确型号且只需结构化属性，优先推荐使用 query_product_spec 等精确结构化工具以降低时延。",
        parameters={"query": "string，检索关键词或自然语言问题", "top_k": "int，返回数量（1-30）"},
    ),
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

    # 1. 参数强类型校验与幻觉阻断 (extra="forbid")
    model_cls = _TOOL_ARG_MODELS.get(payload.tool_name)
    validated_args: Dict[str, Any] = payload.arguments or {}
    if model_cls is not None:
        try:
            parsed = model_cls.model_validate(validated_args)
            validated_args = parsed.model_dump(mode="python")
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"工具 [{payload.tool_name}] 参数校验失败: {exc.errors()}"
            ) from exc

    # 2. 工具调用参数指纹去重与死锁熔断
    caller_id = str((payload.user_context or {}).get("session_id") or user.user_id)
    try:
        _DEADLOCK_BREAKER.check_and_record(caller_id, payload.tool_name, validated_args)
    except DeadlockDetectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = handler(validated_args, _permission(payload, user))
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
