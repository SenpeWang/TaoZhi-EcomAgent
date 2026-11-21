"""判定问答对会话证据、上一问和实时业务对象的依赖。"""
from __future__ import annotations

import re


def refers_to_previous_question(question: str) -> bool:
    """短指代问句需要用上一问补全检索词。"""
    return len(question) <= 12 and bool(re.search(r"^那|它|这个|里面|该|此", question))


def recalls_previous_evidence(question: str, conversation_id: str) -> bool:
    """显式会话和隐式追问都可能继承历史引用。"""
    return bool(conversation_id or re.search(r"刚才|上面|这款|那个|它的|继续|价格呢|还有呢", question))


def requested_business_resources(question: str, payload: dict) -> dict[str, str]:
    """返回需要查询的业务资源和编号，空编号表示需要向用户索取。"""
    resources = {}
    for kind, field, pattern in (
        ("stock", "sku_id", "实时库存|库存多少|可用库存|现价"),
        ("orders", "order_id", "订单状态|查订单|订单发货"),
    ):
        identifier = payload.get(field) or ""
        if identifier or re.search(pattern, question):
            resources[kind] = identifier
    return resources
