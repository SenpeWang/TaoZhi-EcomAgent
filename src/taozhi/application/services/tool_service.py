"""面向当前用户的库存、订单和企业资料查询工具。"""
from __future__ import annotations

from typing import Any

from taozhi.domain.entities import Principal
from taozhi.infrastructure.business_gateway import lookup
from taozhi.infrastructure.persistence.database import database_connection


def execute_tool(p: Principal, *, name: str, query: str, identifier: str) -> dict[str, Any]:
    """执行前端直接调用的受控业务查询或企业文档检索工具。"""
    if name in ("query_stock", "query_order"):
        return lookup(p, "stock" if name == "query_stock" else "orders", identifier)

    from taozhi.infrastructure.knowledge.retriever import search_documents

    with database_connection() as c:
        return {"items": search_documents(c, p, query, 8)}
