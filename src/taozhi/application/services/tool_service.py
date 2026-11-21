"""面向当前用户的库存、订单和企业资料查询工具。"""
from __future__ import annotations

from typing import Any

from taozhi.domain.entities import Principal
from taozhi.knowledge.business import lookup
from taozhi.knowledge.db import database_connection


def execute_tool(p: Principal, *, name: str, query: str, identifier: str) -> dict[str, Any]:
    """执行前端直接调用的受控业务查询或企业文档检索工具。"""
    with database_connection() as c:
        if name in ("query_stock", "query_order"):
            return lookup(c, p, "stock" if name == "query_stock" else "orders", identifier)
        from taozhi.knowledge.retriever import search_documents

        return {"items": search_documents(c, p, query, 8)}
