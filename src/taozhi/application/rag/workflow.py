"""企业知识问答受控检索、多专家核验与答复发布状态机。"""
from __future__ import annotations

from typing import Literal

from taozhi.application.common.task_guard import traced_node, validate_task_execution
from taozhi.domain.rag_verification import cited_paragraphs
from .intent import plan_node
from .retrieval import prepare_evidence, retrieval_node, retry_retrieval
from .specialists import domain_node, quality_node
from .state import State


def publish_node(state: State) -> dict:
    """发布前校验来源并渲染结构化结论、原文受限摘要或业务数据快照。"""
    _, job = validate_task_execution(state, "发布前校验权限与来源", 95)

    if state["hard_block"]:
        answer = "当前没有足够的授权资料，无法给出事实结论：相关内容可能未标注、待确认，或您当前无权查看。请补充资料，或联系负责人申请访问权限。"
    elif state["findings"]:
        answer = "".join(cited_paragraphs(state["question"], state["evidence"], state["findings"]))
    elif state["external_blocked"]:
        answer = "当前资料未获外部 AI 处理许可，以下为授权原文，请人工核验。\n\n" + "\n\n".join(
            e["quote"][:500] + f" [{i + 1}]" for i, e in enumerate(state["evidence"][:5])
        )
    else:
        refs = "；".join(f"《{e.get('title', '')}》" for e in state["evidence"][:3])
        answer = (
            "未能从授权资料中形成核验结论：相关内容在当前资料中可能未标注、待确认。\n"
            + f"已检索到可能相关的资料：{refs}。可换个问法追问细节，或联系资料负责人补充。"
        )
    for b in state["business"]:
        if not b.get("available"):
            answer += "\n\n" + b["message"] + "。"
        else:
            record = b["record"]
            labels = {
                "available": "可用库存",
                "price_minor": "销售价格（分）",
                "status": "订单状态",
                "as_of": "数据时间",
                "sku_id": "商品编号",
                "order_id": "订单编号",
                "currency": "币种",
                "phone": "联系电话",
                "address": "收货地址",
                "customer_name": "客户",
                "purchase_cost_minor": "采购成本（分）",
            }
            statuses = {"CNY": "人民币", "pending": "待支付", "paid": "已支付", "shipped": "已发货", "delivered": "已签收", "cancelled": "已取消", "refunded": "已退款"}
            answer += "\n\n实时业务数据：\n" + "\n".join(f"{labels.get(k, k)}：{statuses.get(str(v), v)}" for k, v in record.items())
    max_level = max([job["input_level"]] + [e["level"] for e in state["evidence"]] + [b.get("source", {}).get("level", 1) for b in state["business"]])
    return {"answer": answer, "input_level": max_level}


def _after_retrieval(state: State) -> Literal["retrieval_retry", "prepare_evidence"]:
    if state["retrieval_decision"] in {"retry_weak_evidence", "retry_low_confidence"}:
        return "retrieval_retry"
    return "prepare_evidence"


def _after_evidence(state: State) -> Literal["specialists", "publication"]:
    return "specialists" if state["retrieval_decision"] == "run_specialists" else "publication"


def build_rag_workflow(checkpointer):
    """编译首次检索、单次补查、证据授权分流、协同核验与发布的有向无环图。"""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(State)
    nodes = [
        ("planning", plan_node),
        ("retrieval", retrieval_node),
        ("retrieval_retry", retry_retrieval),
        ("prepare_evidence", prepare_evidence),
        ("specialists", domain_node),
        ("quality", quality_node),
        ("publication", publish_node),
    ]
    for name, func in nodes:
        builder.add_node(name, traced_node(name, func))
    builder.add_edge(START, "planning")
    builder.add_edge("planning", "retrieval")
    builder.add_conditional_edges("retrieval", _after_retrieval)
    builder.add_edge("retrieval_retry", "prepare_evidence")
    builder.add_conditional_edges("prepare_evidence", _after_evidence)
    builder.add_edge("specialists", "quality")
    builder.add_edge("quality", "publication")
    builder.add_edge("publication", END)
    return builder.compile(checkpointer=checkpointer)
