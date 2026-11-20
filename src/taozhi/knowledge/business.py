"""受控实时业务系统查询客户端（库存 / 订单）。

职责：仅以只读方式对接企业业务接口，严格校验租户、归属节点、时间窗口与敏感字段脱敏。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from openinference.semconv.trace import SpanAttributes
from opentelemetry.trace import StatusCode

from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.observability.tracing import node_span

_ALLOWED_FIELDS = {
    "tenant_id",
    "org_tag",
    "node_id",
    "sku_id",
    "order_id",
    "assigned_user_ids",
    "as_of",
    "available",
    "price_minor",
    "status",
    "currency",
    "customer_name",
    "phone",
    "address",
    "purchase_cost_minor",
}
_VALID_STATUSES = {
    "pending",
    "paid",
    "processing",
    "shipped",
    "delivered",
    "completed",
    "cancelled",
    "refunding",
    "refunded",
    "closed",
    "returned",
}


def _validate_and_redact_business_payload(
    raw: Any,
    p: Principal,
    kind: str,
    identifier: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验业务网关回传载荷合法性，并按当前主体角色执行最小必要脱敏。"""
    if not isinstance(raw, dict) or (set(raw) - _ALLOWED_FIELDS):
        raise ValueError("业务响应包含未登记字段")
    tenant = raw.get("tenant_id") or raw.get("org_tag")
    if tenant != p.tenant_id or raw.get("node_id") not in p.nodes:
        raise ValueError("租户或组织归属不匹配")
    id_field = "sku_id" if kind == "stock" else "order_id"
    if raw.get(id_field) != identifier:
        raise ValueError("业务单据编号与请求不一致")
    if kind == "orders" or "status" in raw:
        if not isinstance(raw.get("status"), str) or raw["status"] not in _VALID_STATUSES:
            raise ValueError("订单状态非法")
    assigned = raw.get("assigned_user_ids", [])
    if (
        not isinstance(assigned, list)
        or len(assigned) > 100
        or any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in assigned)
    ):
        raise ValueError("归属坐席列表格式异常")
    if "phone" in raw and (not isinstance(raw["phone"], str) or not re.fullmatch(r"[+0-9() -]{7,24}", raw["phone"])):
        raise ValueError("手机号格式校验失败")
    asof = datetime.fromisoformat(raw["as_of"].replace("Z", "+00:00"))
    if asof.tzinfo is None or not -5 <= (datetime.now(timezone.utc) - asof).total_seconds() <= 60:
        raise ValueError("业务快照时间窗过期")

    node = raw["node_id"]
    grade = p.grade(node)
    if not p.boss and (grade < 1 or (kind == "orders" and not p.manages(node) and p.id not in assigned)):
        return {}, {}

    result: dict[str, Any] = {
        k: raw[k]
        for k in ("sku_id", "order_id", "available", "price_minor", "status", "currency", "as_of")
        if k in raw
    }
    for key in ("available", "price_minor"):
        if key in result and (type(result[key]) is not int or result[key] < 0):
            raise ValueError("库存或价格字段类型非法")
    if result.get("currency", "CNY") != "CNY":
        raise ValueError("不支持的非人民币币种")
    if "phone" in raw:
        phone = str(raw["phone"])
        result["phone"] = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else "已隐藏"
    if "address" in raw:
        result["address"] = "详细地址已隐藏"
    if p.manages(node) and "customer_name" in raw:
        result["customer_name"] = f"{str(raw['customer_name'])[:1]}某"
    if p.boss and "purchase_cost_minor" in raw:
        if type(raw["purchase_cost_minor"]) is not int or raw["purchase_cost_minor"] < 0:
            raise ValueError("采购成本字段非法")
        result["purchase_cost_minor"] = raw["purchase_cost_minor"]

    source = {
        "kind": "business",
        "tenant_id": p.tenant_id,
        "node_id": node,
        "level": 3 if "purchase_cost_minor" in result else 1,
        "as_of": asof.timestamp(),
        "resource": kind,
        "identifier": identifier,
        "assigned_user_ids": [p.id] if p.id in assigned else [],
    }
    return result, source


def lookup(
    conn: Any,
    p: Principal,
    kind: str,
    identifier: str,
    transport: Any = None,
) -> dict[str, Any]:
    """查询受控库存或订单实时状态，未配置或权限不符时安全降级。

    Args:
        conn: 数据库连接占位参数（保持与仓内工具接口一致）。
        p: 当前请求主体。
        kind: 业务域名称（``stock`` 或 ``orders``）。
        identifier: 商品 SKU 或订单编号。
        transport: 可选测试替换 HTTP Transport。

    Returns:
        结构化业务响应字典，包含 ``available``、``message`` 及脱敏后的 ``record`` / ``source``。
    """
    _ = conn
    from ..config import get_settings

    settings = get_settings()
    if not settings.business_api_url or not settings.business_api_token:
        return {"available": False, "message": "未接入真实订单、库存或财务系统"}
    base = urlparse(settings.business_api_url)
    if (
        kind not in ("stock", "orders")
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
        or base.scheme != "https"
        or not base.hostname
        or base.query
        or base.fragment
    ):
        raise Denied("业务工具配置或标识无效", "BUSINESS_INVALID", 400)

    try:
        endpoint = f"{settings.business_api_url.rstrip('/')}/{kind}/{quote(identifier, safe='')}"
        headers = {
            "Authorization": f"Bearer {settings.business_api_token}",
            "X-Org-Tag": quote(p.tenant_id, safe=""),
            "X-User-Id": p.id,
        }
        with node_span("business.lookup", "TOOL", {SpanAttributes.TOOL_NAME: kind}) as span:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10, transport=transport) as client:
                resp = client.get(endpoint, headers=headers)
            if resp.status_code != 200:
                if span is not None:
                    span.set_attribute("error.type", "BusinessServiceError")
                    span.set_attribute("tool.status", "error")
                    span.set_status(StatusCode.ERROR)
                return {"available": False, "message": "业务服务暂时不可用或记录无访问权限"}
            result, source = _validate_and_redact_business_payload(resp.json(), p, kind, identifier)
            if span is not None:
                span.set_attribute("tool.result_count", int(bool(result)))
                span.set_attribute("tool.status", "ok" if result else "empty")
            if not result:
                return {"available": False, "message": "业务记录不存在或没有访问权限"}
            return {"available": True, "message": "已读取实时业务数据", "record": result, "source": source}
    except Exception:
        return {"available": False, "message": "业务响应校验失败或连接超时"}
