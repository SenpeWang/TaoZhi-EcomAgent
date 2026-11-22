"""ChatBI 受控问数 LangGraph 工作流组装。"""
from __future__ import annotations

from typing import Any

from taozhi.application.common.task_guard import empty_token_usage, traced_node, validate_task_execution
from taozhi.domain.errors import Denied
from taozhi.infrastructure.persistence.access_repo import can_read_metric
from taozhi.infrastructure.persistence.bi_repo import get_published_metric_by_id
from taozhi.infrastructure.persistence import metric_execution
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.observability.tracing import node_span
from .intent import detect_data_intent
from .state import BiState


def bi_intent_node(state: BiState) -> dict:
    """解析或复用预识别的受控指标意图（零外部 LLM 调用）。"""
    p, job = validate_task_execution(state, "解析语义层指标意图", 15)
    q = state.get("question") or job["question"]
    di = state.get("data_intent")
    if not di:
        with database_connection() as c:
            di = detect_data_intent(c, p, q) or {}
    return {
        "question": q,
        "data_intent": di,
        "input_level": state.get("input_level") or job["input_level"],
        "token_usage": state.get("token_usage") or empty_token_usage(),
    }


def data_query_node(state: BiState) -> dict:
    """问数节点：受控执行语义层指标，SQL 永远由白名单模板组装。"""
    p, job = validate_task_execution(state, "执行受控指标查询（语义层）", 50)
    di = state.get("data_intent") or {}
    result: dict = {"denied": True, "message": di.get("reason") or "未找到可回答该问题的指标。"}
    level = 1
    sources = []
    if di.get("metric_key"):
        with database_connection() as c:
            m = get_published_metric_by_id(c, p.tenant_id, di["metric_id"])
            if not m or not can_read_metric(c, p, m):
                result = {"denied": True, "message": "该指标不存在，或您当前的角色与密级无权查看。"}
            else:
                try:
                    with node_span("bi.query_metric", "RETRIEVER") as span:
                        cols, rows, meta = metric_execution.execute_metric(c, p, m, di.get("intent") or {})
                        if span is not None:
                            span.set_attribute("retrieval.result_count", len(rows))
                    result = {
                        "denied": False,
                        "columns": cols,
                        "rows": metric_execution.jsonable_rows(rows),
                        "meta": meta,
                        "metric_key": m["metric_key"],
                        "metric_name": m["name"],
                        "unit": m["unit"],
                        "description": m["description"],
                    }
                    level = m["level"]
                    sources.append({"kind": "metric", "metric_id": m["id"], "tenant_id": p.tenant_id})
                except Denied as e:
                    result = {"denied": True, "message": f"指标执行被安全策略拒绝：{e}"}
                except Exception:
                    result = {"denied": True, "message": "指标查询暂时不可用，请稍后重试。"}
    return {
        "data_result": result,
        "sources": sources,
        "hard_block": result.get("denied", False),
        "input_level": max(job["input_level"], level),
    }


def bi_publish_node(state: BiState) -> dict:
    """渲染受控指标表格结果或权限拒绝提示。"""
    _, job = validate_task_execution(state, "发布前校验指标权限", 95)
    dr = state.get("data_result") or {}
    if dr.get("denied", True):
        answer = dr.get("message", "该问题暂无法通过指标数据回答。") + "\n\n您也可以换个问法，或改从知识库资料中查找。"
    else:
        cols, rows, meta = dr["columns"], dr["rows"], dr["meta"]
        head = "、".join(str(x) for x in cols)
        lines = [
            f"「{dr['metric_name']}」查询结果（{meta.get('unit','')}，共 {len(rows)} 行，耗时 {meta.get('elapsed_ms','?')}ms）：",
            "",
            head,
            "─" * min(60, max(24, len(head) * 2)),
        ]
        lines.extend(" | ".join(str(x) for x in r) for r in rows[:15])
        if len(rows) > 15:
            lines.append(f"...（其余 {len(rows) - 15} 行已省略）")
        lines.extend(
            [
                "",
                f"口径说明：{dr['description']}",
                f"数据来源：企业指标语义层（{dr['metric_key']}），受控只读查询，密级 L{meta.get('level',1)}。",
            ]
        )
        answer = "\n".join(lines)
    return {
        "answer": answer,
        "input_level": max(state.get("input_level", job["input_level"]), 1),
        "hard_block": bool(dr.get("denied", True)),
    }


def build_bi_workflow(checkpointer):
    """构建 ChatBI 受控问数 LangGraph 执行图。"""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(BiState)
    for name, func in (
        ("bi_intent", bi_intent_node),
        ("data_query", data_query_node),
        ("bi_publish", bi_publish_node),
    ):
        builder.add_node(name, traced_node(name, func, span_prefix="bi."))
    builder.add_edge(START, "bi_intent")
    builder.add_edge("bi_intent", "data_query")
    builder.add_edge("data_query", "bi_publish")
    builder.add_edge("bi_publish", END)
    return builder.compile(checkpointer=checkpointer)


def build_bi_result(state: dict[str, Any]) -> dict[str, Any]:
    """将 ChatBI 问数工作流终态组装为前端可消费的结果负载。"""
    dr = state.get("data_result") or {}
    result: dict[str, Any] = dict(
        answer=state["answer"],
        citations=[],
        specialists=[{"name": "指标语义层（受控 SQL）", "count": 1}] if (dr and not dr.get("denied")) else [],
        missing=[],
        hard_block=bool(state.get("hard_block")),
        external_blocked=False,
        model_calls=0,
        usage=[],
        cache_hit=False,
        token_usage=state.get("token_usage") or empty_token_usage(),
        degraded={},
    )
    if dr and not dr.get("denied"):
        result["data"] = dr
    return result
