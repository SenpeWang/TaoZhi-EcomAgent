"""问答规划、多专家路由选择与 ChatBI 意图识别编排节点。"""
from __future__ import annotations

import re
from typing import Any

from taozhi.domain.privacy import redact
from taozhi.domain.rag_verification import SPECIALIST_AGENTS
from taozhi.domain.ranking import tokenize_zh
from taozhi.infrastructure.persistence.bi_repo import list_published_metrics, load_bi_dimensions
from taozhi.knowledge import metrics as metrics_mod
from taozhi.knowledge.db import database_connection
from taozhi.orchestration.decision import get_decision_engine
from .state import State, empty_token_usage, validate_task_execution

_DATA_KEYWORDS = re.compile(
    "销售额|销量|GMV|营业额|成交|订单量|单量|退货率|退款率|售后率|售后工单|退换货数量"
    "|毛利|利润|采购成本|进货成本|进货额|底价|库存|补货|日销售|趋势|客单价"
    "|有多少|多少个|多少单|卖了多少|赚了多少|分布"
)

_SPECIALIST_PATTERNS = [
    ("product", ["规格", "参数", "型号", "尺寸", "切幅", "材质", "材质说明", "功能", "兼容", "区别", "对比"]),
    ("after_sales", ["售后", "退货", "保修", "保修期", "换新", "运费", "维修", "投诉"]),
    ("operations", ["主图", "文案", "卖点", "上架", "违禁词", "详情页"]),
    ("supply", ["库存", "发货", "起订", "供货", "现货", "交期"]),
    ("finance", ["毛利", "成本", "底价", "结算"]),
]


def _parse_time_range(q: str) -> dict:
    m = re.search(r"近\s*(\d+)\s*天", q)
    if m:
        return {"days": min(int(m.group(1)), 365)}
    if re.search("本周|这一周|这周|近一周|最近一周|上周", q):
        return {"days": 7}
    if re.search("近两周|最近两周", q):
        return {"days": 14}
    if re.search("本月|这个月|近一月|最近一月", q):
        return {"days": 30}
    if re.search("近三月|最近三月|这个季度", q):
        return {"days": 90}
    if re.search("今年|近半年|最近半年", q):
        return {"days": 183}
    return {"days": 30}


def _intent_of(conn: Any, p: Any, m: dict, q: str) -> dict:
    """由命中指标构造受控执行意图（维度过滤从 DB 动态识别 + 时间范围）。"""
    dims = set(m["dimensions"])
    filters = {}
    channels, categories = load_bi_dimensions(conn, p.tenant_id)
    for word in sorted(channels, key=len, reverse=True):
        if word in q and "channel_name" in dims:
            filters["channel_name"] = channels[word]
            break
    for cat in categories:
        if cat in q and "category" in dims:
            filters["category"] = cat
            break
    return {
        "metric_key": m["metric_key"],
        "intent": {"metric_key": m["metric_key"], "filters": filters, "time_range": _parse_time_range(q)},
        "metric_id": m["id"],
        "metric_name": m["name"],
        "metric_level": m["level"],
    }


def _build_data_intent(conn: Any, p: Any, q: str) -> dict | None:
    """构造问数意图：别名强匹配（含越权检测）→ 词重叠弱匹配（阈值门控）。"""
    if not _DATA_KEYWORDS.search(q):
        return None
    all_m = list_published_metrics(conn, p.tenant_id)
    if not all_m:
        return None
    visible_ids = {m["id"] for m in metrics_mod.list_metrics(conn, p)}
    strong = []
    for m in all_m:
        names = [m["name"]] + [a for a in (m["aliases"] or []) if a]
        hit = next((n for n in sorted(names, key=len, reverse=True) if n in q), None)
        if hit:
            strong.append((len(hit), m))
    if strong:
        strong.sort(key=lambda x: -x[0])
        _, m = strong[0]
        if m["id"] not in visible_ids:
            return {
                "denied": True,
                "reason": f"「{m['name']}」是受限指标（密级 L{m['level']}），您当前的角色无权查看。如需访问请联系指标负责人申请授权。",
            }
        return _intent_of(conn, p, m, q)

    terms = set(tokenize_zh(q))
    scored = []
    for m in metrics_mod.list_metrics(conn, p):
        toks = set(tokenize_zh(m["metric_key"] + " " + m["name"] + " " + " ".join(m["aliases"]) + " " + m["description"]))
        score = len(terms & toks) / max(1, len(terms))
        if score >= 0.4:
            scored.append((score, m))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return _intent_of(conn, p, scored[0][1], q)


def plan_node(state: State) -> dict:
    """意图理解与多智能体路由规划（零外部模型调用，附带 ChatBI 意图识别）。"""
    p, job = validate_task_execution(state, "动态意图理解与多智能体路由规划", 10)
    q = job["question"]
    options = {k: f"{v[0]}：{v[1]}" for k, v in SPECIALIST_AGENTS.items()}
    decision = get_decision_engine().choice(
        context=redact(q),
        options=options,
        multi_select=False,
        top_k=3,
        force_llm=False,
        allow_llm=False,
    )
    selected_agents = [r for r in decision.selected if r in SPECIALIST_AGENTS]
    for role, patterns in _SPECIALIST_PATTERNS:
        if any(w in q for w in patterns):
            selected_agents.append(role)
    if not selected_agents or re.search("规格|尺寸|型号|参数|幅宽|切幅|材质", q):
        selected_agents.insert(0, "product")
    selected_agents = list(dict.fromkeys(selected_agents))[:3]

    data_intent = None
    try:
        with database_connection() as c:
            data_intent = _build_data_intent(c, p, q)
    except Exception:
        data_intent = None

    return dict(
        question=q,
        selected_agents=selected_agents or ["product"],
        model_calls=0,
        missing=[],
        findings=[],
        sources=[],
        business=[],
        usage=[],
        input_level=job["input_level"],
        data_intent=data_intent or {},
        timing={},
        degraded={},
        token_usage=empty_token_usage(),
    )
