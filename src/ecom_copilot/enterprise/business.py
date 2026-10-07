from datetime import datetime,timezone
from urllib.parse import quote,urlparse
import httpx,re
from .policy import Denied
def lookup(conn,p,kind,identifier,transport=None):
    from ..config import get_settings
    s=get_settings()
    if not s.business_api_url or not s.business_api_token:return {"available":False,"message":"未接入真实订单、库存或财务系统"}
    base=urlparse(s.business_api_url)
    if kind not in ("stock","orders") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",identifier) or base.scheme!="https" or not base.hostname or base.query or base.fragment:
        raise Denied("业务工具配置或标识无效","BUSINESS_INVALID",400)
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=10,transport=transport) as client:
            r=client.get(s.business_api_url.rstrip("/")+"/"+kind+"/"+quote(identifier,safe=""),
              headers={"Authorization":"Bearer "+s.business_api_token,"X-Org-Tag":quote(p.tenant_id,safe=""),"X-User-Id":p.id})
        if r.status_code!=200:return {"available":False,"message":"业务服务暂时不可用或记录无访问权限"}
        raw=r.json()
        allowed={"tenant_id","org_tag","node_id","sku_id","order_id","assigned_user_ids","as_of","available","price_minor","status","currency","customer_name","phone","address","purchase_cost_minor"}
        if not isinstance(raw,dict) or set(raw)-allowed:raise ValueError()
        tenant=raw.get("tenant_id") or raw.get("org_tag")
        if tenant!=p.tenant_id or raw.get("node_id") not in p.nodes:raise ValueError()
        if raw.get("sku_id" if kind=="stock" else "order_id")!=identifier:raise ValueError()
        statuses={"pending","paid","processing","shipped","delivered","completed","cancelled","refunding","refunded","closed","returned"}
        if kind=="orders" or "status" in raw:
            if not isinstance(raw.get("status"),str) or raw["status"] not in statuses:raise ValueError()
        assigned=raw.get("assigned_user_ids",[])
        if not isinstance(assigned,list) or len(assigned)>100 or any(not isinstance(x,str) or not 1<=len(x)<=128 for x in assigned):raise ValueError()
        if "phone" in raw and (not isinstance(raw["phone"],str) or not re.fullmatch(r"[+0-9() -]{7,24}",raw["phone"])):raise ValueError()
        asof=datetime.fromisoformat(raw["as_of"].replace("Z","+00:00"))
        if asof.tzinfo is None or not -5<=(datetime.now(timezone.utc)-asof).total_seconds()<=60:raise ValueError()
        node=raw["node_id"];grade=p.grade(node)
        if not p.boss and (grade<1 or (kind=="orders" and not p.manages(node) and p.id not in raw.get("assigned_user_ids",[]))):
            return {"available":False,"message":"业务记录不存在或没有访问权限"}
        result={k:raw[k] for k in ("sku_id","order_id","available","price_minor","status","currency","as_of") if k in raw}
        for key in ("available","price_minor"):
            if key in result and (type(result[key]) is not int or result[key]<0):raise ValueError()
        if result.get("currency","CNY")!="CNY":raise ValueError()
        if "phone" in raw:
            phone=str(raw["phone"]);result["phone"]=phone[:3]+"****"+phone[-4:] if len(phone)>=7 else "已隐藏"
        if "address" in raw:result["address"]="详细地址已隐藏"
        if p.manages(node) and "customer_name" in raw:result["customer_name"]=str(raw["customer_name"])[:1]+"某"
        if p.boss and "purchase_cost_minor" in raw:
            if type(raw["purchase_cost_minor"]) is not int or raw["purchase_cost_minor"]<0:raise ValueError()
            result["purchase_cost_minor"]=raw["purchase_cost_minor"]
        source=dict(kind="business",tenant_id=p.tenant_id,node_id=node,level=3 if "purchase_cost_minor" in result else 1,as_of=asof.timestamp(),resource=kind,identifier=identifier,assigned_user_ids=[p.id] if p.id in assigned else [])
        return {"available":True,"message":"已读取实时业务数据","record":result,"source":source}
    except Exception:
        return {"available":False,"message":"业务响应校验失败或连接超时"}
