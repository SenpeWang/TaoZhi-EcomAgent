"""Read-only OMS/ERP contract. Backend identity is checked before returning data.

No order mutations, refunds or inventory deductions are exposed to agents.
Missing integrations produce explicit unavailable results, never simulated data.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote, urlparse
import httpx
from pydantic import BaseModel, ConfigDict, Field

class StockSnapshot(BaseModel):
    model_config=ConfigDict(extra="forbid")
    org_tag:str
    sku_id:str
    available:int=Field(ge=0)
    price_minor:int=Field(ge=0)
    currency:Literal["CNY"]="CNY"
    as_of:datetime

class OrderSnapshot(BaseModel):
    model_config=ConfigDict(extra="forbid")
    org_tag:str
    customer_id:str
    order_id:str
    status:Literal["pending","paid","shipped","delivered","cancelled","refunded"]
    as_of:datetime

class BusinessUnavailable(RuntimeError):
    pass

class BusinessClient:
    def __init__(self,settings,transport=None):
        self.settings=settings
        self.transport=transport
    def lookup(self,kind,identifier,permission):
        if not self.settings.business_api_url or not self.settings.business_api_token:
            raise BusinessUnavailable("未配置企业订单/库存系统")
        parsed=urlparse(self.settings.business_api_url)
        if parsed.scheme!="https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise BusinessUnavailable("业务服务必须使用固定 HTTPS 地址")
        if not identifier or len(identifier)>64 or any(x in identifier for x in ("/","\\","..")):
            raise BusinessUnavailable("业务标识格式不正确")
        if kind not in ("stock","orders"):raise BusinessUnavailable("未知只读业务工具")
        path=kind+"/"+quote(identifier,safe="")
        try:
            with httpx.Client(transport=self.transport,trust_env=False,follow_redirects=False,timeout=10) as client:
                response=client.get(self.settings.business_api_url.rstrip("/")+"/"+path,
                    headers={"Authorization":"Bearer "+self.settings.business_api_token,
                             "X-Org-Tag":quote(permission.org_tag,safe=""),
                             "X-User-Id":quote(permission.user_id,safe="")})
                if response.status_code!=200:raise BusinessUnavailable("业务服务暂时不可用或记录无访问权限")
                schema=StockSnapshot if kind=="stock" else OrderSnapshot
                record=schema.model_validate(response.json())
        except BusinessUnavailable:raise
        except Exception:raise BusinessUnavailable("业务服务请求或响应校验失败") from None
        if record.org_tag!=permission.org_tag:
            raise BusinessUnavailable("业务数据所属企业不匹配")
        if kind=="orders" and (record.customer_id!=permission.user_id or record.order_id!=identifier):
            raise BusinessUnavailable("订单归属不匹配")
        if kind=="stock" and record.sku_id!=identifier:
            raise BusinessUnavailable("SKU 不匹配")
        if record.as_of.tzinfo is None:
            raise BusinessUnavailable("业务数据时间必须包含时区")
        age=(datetime.now(timezone.utc)-record.as_of).total_seconds()
        if age < -5 or age>self.settings.business_snapshot_max_age:
            raise BusinessUnavailable("业务快照已过期或时间异常")
        return record
