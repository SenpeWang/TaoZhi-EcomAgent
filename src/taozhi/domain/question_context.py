"""判定问答对会话证据、上一问和实时业务对象的依赖。"""
from __future__ import annotations

import re

_BUSINESS_FIELDS = (
    ("stock", "available",
     r"实时库存|可用库存|(?:当前|目前|现在)(?:的)?库存|库存(?:还有|有|剩余|剩)?(?:多少|几件|几台|几套)|还有多少库存"
     r"|(?:有没有|还有|有无)(?:现货|货)|有(?:现货|货)[吗么]|什么时候有现货|^(?:那|它的|这款的?)?库存呢?$"),
    ("stock", "price_minor",
     r"现价|实时(?:售价|价格)|(?:当前|现在|今日|今天|目前)(?:的)?(?:售价|价格|卖多少钱|多少钱)"
     r"|(?:售价|价格|卖价|批发价|成交价|会员价|到手价|报价)(?:是|为)?(?:多少|几块|几元)"
     r"|(?:这款|这件|本品|这个商品)(?:商品)?多少钱|卖多少钱|^(?:那|它的|这款的?)?(?:价格|售价|多少钱)呢?$"),
    ("orders", "status",
     r"订单状态|查(?:询)?订单|(?:订单|这单|我的单).{0,16}(?:到哪|签收|发货[了没吗么]|发[了没]吗|什么状态|什么进度)"),
)
_NONCURRENT = r"历史|截至|截止|昨天|昨日|前天|上周|上月|去年|前年|明天|明日|下周|下月|明年|预计|预测|什么时候有现货|(?:19|20)\d{2}年|\d{1,2}月\d{1,2}日"
_FIELD_TERMS = {"available": r"库存|现货", "price_minor": r"价格|售价|卖价|零售价|卖多少钱", "status": r"订单状态|订单的状态|订单进度"}
_BUSINESS_DEFINITION = re.compile(
    r"(?:什么是|如何查(?:询)?|怎么查(?:询)?)(?:库存|价格|订单状态)"
    r"|(?:库存|价格|订单状态)的?(?:定义|管理制度|管理流程)|订单状态(?:有哪几种|有哪些|包括|指什么|是什么意思)"
)


def refers_to_previous_question(question: str) -> bool:
    """短指代问句需要用上一问补全检索词。"""
    return len(question) <= 12 and bool(re.search(r"^那|它|这个|里面|该|此", question))


def recalls_previous_evidence(question: str, conversation_id: str) -> bool:
    """显式会话和隐式追问都可能继承历史引用。"""
    return bool(conversation_id or re.search(r"刚才|上面|这款|那个|它的|继续|价格呢|还有呢", question))


def business_field_requests(question: str) -> dict[str, dict[str, str]]:
    """将具体字段请求映射到连接器能力，保留不能由当前快照核实的原因。"""
    result: dict[str, dict[str, str]] = {}
    for clause in re.split(r"[，,。！？?；;\n]", _BUSINESS_DEFINITION.sub("", question)):
        clause = clause.strip()
        dated = re.search(_NONCURRENT, clause)
        for resource, field, pattern in _BUSINESS_FIELDS:
            if not re.search(pattern, clause) and not (dated and re.search(_FIELD_TERMS[field], clause)):
                continue
            reason = ""
            if dated:
                reason = "当前快照不能核实指定时点的值，请提供该时点的业务记录或已确认的计划。"
            elif field == "price_minor" and re.search(r"采购|成本|供货|供应商|批发|成交|会员|到手价", clause):
                reason = "此接口仅提供商品销售价格，请提供对应报价单、订单或交易凭据。"
            fields = result.setdefault(resource, {})
            fields[field] = fields.get(field) or reason
    return result


def requested_business_resources(question: str, payload: dict) -> dict[str, str]:
    """返回需要查询的业务资源和编号，空编号表示需要向用户索取。"""
    resources = {}
    live = business_field_requests(question)
    for kind, field in (("stock", "sku_id"), ("orders", "order_id")):
        identifier = payload.get(field) or ""
        if identifier or kind in live:
            resources[kind] = identifier
    return resources


def requests_record_overview(question: str, resource: str) -> bool:
    """只有明确要求查看编号对应的业务概览时，才向文档答复附加业务快照。"""
    specific = re.findall(r"(商品|库存|订单)(?:的)?(?:业务(?:数据)?)?(?:概览|总览|快照)", question)
    if specific:
        return any(("orders" if name == "订单" else "stock") == resource for name in specific)
    if re.search(r"业务(?:数据)?(?:概览|总览|快照)", question):
        return True
    return bool(re.fullmatch(r"(?:请)?(?:查看|提供)?(?:该编号的?)?(?:资料|记录)(?:概览|说明|详情)", question.strip(" ？?。")))


def extract_search_keyword(user_query: str) -> str:
    """从自然语言指令中提炼最精准的核心商品品类或搜索短语。

    Args:
        user_query: 用户原始的电商调研提问或检索诉求。

    Returns:
        提炼后的紧凑核心关键词。
    """
    q = re.sub(r"[，。！？、；：\"'“”‘’()（）【】\[\]]+", " ", user_query)
    stop_phrases = [
        "帮我", "请帮我", "调研一下", "调研", "分析一下", "分析", "市场趋势", "和", "与",
        "竞争格局", "消费者痛点", "差评原因", "卖点", "怎么样", "如何", "我想了解",
        "目前", "2026年", "2025年", "最新的", "行情", "情况", "数据", "搜一下",
        "查一下", "查找", "检索", "市场", "电商", "行业", "怎么看", "值不值得",
        "多少钱", "价格是多少", "是什么", "有哪些", "在", "上", "的", "怎么",
        "适合", "能用吗", "可以用", "是不是", "推荐", "对比", "为什么",
    ]
    for sp in stop_phrases:
        q = q.replace(sp, " ")
    cleaned = re.sub(r"\s+", " ", q).strip()
    return cleaned if len(cleaned) >= 2 else user_query.strip()[:30]
