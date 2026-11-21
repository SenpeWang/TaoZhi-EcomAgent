"""工作台概览与经营指标看板的授权查询和结果汇总。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from taozhi.application.services import document_service
from taozhi.config import get_settings, load_config
from taozhi.domain.access import can_task
from taozhi.domain.entities import Principal
from taozhi.infrastructure.persistence import bi_repo, job_repo
from taozhi.infrastructure.persistence.access_repo import batch_sources_validity, can_read_metric
from taozhi.knowledge import metrics as metrics_mod
from taozhi.knowledge.db import database_connection


def get_dashboard_summary(p: Principal) -> dict[str, Any]:
    """汇总工作台文档、问答任务与模型/业务系统就绪度统计。"""
    docs = document_service.list_documents(p)
    with database_connection() as c:
        jobs = job_repo.list_ask_jobs(c, p.tenant_id)
        permitted = [r for r in jobs if can_task(p, r)]
        validity = batch_sources_validity(c, p, permitted)
        visible = [r for r in permitted if validity.get(r["id"], False)]
    settings = get_settings()
    return dict(
        documents=len(docs),
        tasks=len(visible),
        review=sum(r["state"] == "review" for r in visible),
        completed=sum(r["state"] == "completed" for r in visible),
        teams=[p.nodes[m["node_id"]]["name"] for m in p.memberships if m["role"] != "boss"],
        model_ready=bool(settings.api_key),
        business_ready=bool(settings.business_api_url and settings.business_api_token),
        mode=load_config().mode,
    )


def get_bi_dashboard(p: Principal, days: int) -> dict[str, Any]:
    """执行指标语义层受控查询，构造数据中台经营看板视图。"""

    def run(key: str, intent: dict | None = None) -> dict[str, Any] | None:
        with database_connection() as c:
            m = bi_repo.get_published_metric_by_key(c, p.tenant_id, key)
            if not m or not can_read_metric(c, p, m):
                return None
            try:
                cols, rows, meta = metrics_mod.execute_metric(c, p, m, intent or {})
                return dict(
                    columns=cols,
                    rows=metrics_mod.jsonable_rows(rows),
                    meta=meta,
                    name=m["name"],
                    unit=m["unit"],
                    level=m["level"],
                    description=m["description"],
                )
            except Exception:
                return None

    def total(block: dict[str, Any] | None) -> float | None:
        if not block or not block["rows"]:
            return None
        try:
            return round(sum(float(r[-1]) for r in block["rows"] if isinstance(r[-1], (int, float))), 2)
        except (TypeError, ValueError):
            return None

    window = {"time_range": {"days": days}}
    gsv, orders = run("biz.gsv", window), run("biz.order_count", window)
    rate, profit = run("biz.refund_rate", window), run("biz.gross_profit", window)
    low, aftersales = run("biz.low_stock_count"), run("biz.aftersales_count", window)
    trend = run("biz.gsv_daily", window)

    with database_connection() as c:
        latest_order_at = bi_repo.get_latest_order_at(c, p.tenant_id)

    cards: dict[str, Any] = dict(
        gsv_30d=total(gsv),
        orders_30d=total(orders),
        gross_profit_30d=total(profit),
        low_stock=total(low),
        aftersales_count=total(aftersales),
    )
    if rate and rate["rows"]:
        worst = max(rate["rows"], key=lambda r: float(r[-1]) if isinstance(r[-1], (int, float)) else 0)
        cards["worst_refund_channel"] = {"channel": worst[0], "rate": worst[-1]}
        cards["avg_refund_rate"] = round(sum(float(r[-1]) for r in rate["rows"]) / len(rate["rows"]), 2)

    channel_gsv = None
    if gsv and gsv["rows"]:
        agg: dict[str, float] = {}
        for r in gsv["rows"]:
            agg[r[0]] = round(agg.get(r[0], 0) + float(r[-1]), 2)
        channel_gsv = dict(sorted(agg.items(), key=lambda x: -x[1]))

    category_summary = None
    if gsv and gsv["rows"]:
        cat_gsv: dict[str, float] = {}
        for r in gsv["rows"]:
            cat_gsv[r[1]] = round(cat_gsv.get(r[1], 0) + float(r[-1]), 2)
        cat_profit: dict[str, float] = {}
        if profit and profit["rows"]:
            for r in profit["rows"]:
                cat_profit[r[1]] = round(cat_profit.get(r[1], 0) + float(r[-1]), 2)
        category_summary = [
            dict(
                category=cat,
                gsv=val,
                gross_profit=cat_profit.get(cat) if profit and profit["rows"] else None,
            )
            for cat, val in sorted(cat_gsv.items(), key=lambda x: -x[1])
        ]

    return dict(
        days=days,
        latest_order_at=latest_order_at,
        cards=cards,
        trend=trend,
        channel_gsv=channel_gsv,
        category_summary=category_summary,
        refund_rate=rate,
        aftersales=aftersales,
        low_stock=low,
        gross_profit=profit,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
