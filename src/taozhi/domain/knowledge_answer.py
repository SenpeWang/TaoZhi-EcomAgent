"""在内部依据不足时，为通用术语提供有出处的释义，为业务问题指出所需凭据。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

AnswerBasis = Literal[
    "internal_evidence",
    "business_snapshot",
    "source_excerpt",
    "public_reference",
    "general_knowledge",
    "insufficient_evidence",
]
VerificationIssue = Literal["model_unconfigured", "budget_exhausted", "model_unavailable"]


class PublicSource(TypedDict):
    """内置释义所依据的公开原文，由前端作为外部链接展示。"""

    title: str
    url: str


class EvidenceGapAnswer(TypedDict):
    """未能用内部资料形成结论时的答复和后续核验指引。"""

    answer: str
    answer_basis: Literal["public_reference", "insufficient_evidence"]
    guidance: str
    public_sources: list[PublicSource]


@dataclass(frozen=True)
class _Definition:
    terms: tuple[str, ...]
    explanation: str
    source_title: str
    source_url: str


_DEFINITIONS = (
    _Definition(
        ("usb-c", "type-c", "usbtype-c", "usb-c接口", "type-c接口", "usbtype-c接口"),
        "USB-C 是 USB Type-C 连接器的名称。这种插头可以正反插。确认某款设备支持的数据速率或充电功率，还需要查看该设备和线缆的规格。",
        "USB-IF · USB Type-C 线缆与连接器指南",
        "https://www.usb.org/sites/default/files/usb_type-c_language_product_and_packaging_guidelines_20230320.pdf",
    ),
    _Definition(
        ("usbpd", "usb-pd", "usbpowerdelivery", "pd快充", "usbpd快充", "usbpd协议"),
        "USB Power Delivery（USB PD）是一种 USB 供电协议。受电设备可根据需要请求供电，供电端与受电端协商工作条件。"
        "判断某个组合的实际充电功率，还要核对充电器、线缆和设备的规格。",
        "USB-IF · USB Power Delivery",
        "https://www.usb.org/usb-charger-pd",
    ),
    _Definition(
        ("低功耗蓝牙", "蓝牙低功耗", "bluetoothle", "bluetoothlowenergy", "ble"),
        "低功耗蓝牙（Bluetooth LE）是一种面向低功耗通信的无线技术，支持点对点和广播等通信方式。",
        "Bluetooth SIG · 蓝牙技术概览",
        "https://www.bluetooth.com/learn-about-bluetooth/tech-overview/",
    ),
    _Definition(
        ("gtin", "全球贸易项目代码"),
        "GTIN 是全球贸易项目代码，用来唯一标识供应链中可定价、订购或开票的一种商品或服务。",
        "GS1 · 全球贸易项目代码的定义",
        "https://support.gs1.org/support/solutions/articles/43000734283-what-is-a-global-trade-item-number-gtin-",
    ),
    _Definition(
        ("sku", "库存单位"),
        "SKU 是商家为商品或商品变体分配的货号，可用于跟踪库存和统计销售。例如，同一商品的不同颜色可以分别设置 SKU。",
        "Shopify 帮助中心 · 使用 SKU 管理库存",
        "https://help.shopify.com/zh-CN/manual/products/details/sku",
    ),
)

_DEFINITION_QUESTION = re.compile(
    r"(?:请问,?)?(?:(?:什么是|什么叫|(?:请)?(?:介绍|解释)(?:一下)?)(?P<object>.+)"
    r"|(?P<term>.+?)(?:是什么(?:意思)?|指什么|的定义是什么)"
    r"|(?P<typed_term>.+?)是什么(?P<kind>接口|协议))"
)

_EVIDENCE_GUIDANCE = (
    (r"保修|售后|退[换货款]|换新|维修|运费|赔付|保价",
     "请提供购买渠道、购买日期和适用商品，请售后政策负责人补充该渠道当期生效的条款，再核实受理条件。"),
    (r"成本|毛利|利润|营收|销售额|结算|对账|佣金",
     "请明确统计期间和核算对象，请财务负责人提供已确认的报表、计算口径或结算规则，再核对金额。"),
    (r"报价|售价|价格|现价|多少钱|单价|折扣|优惠|采购价",
     "请提供商品编号、销售渠道和询价日期，请价格负责人确认有效价目表或正式报价，再核实金额。"),
    (r"订单|物流|到货|签收",
     "请在有访问权限的订单系统中按订单编号核对记录。涉及物流进度时，请订单负责人提供带更新时间的状态。"),
    (r"库存|交期|发货|供货|起订|采购|供应商",
     "请提供商品编号、仓库或供应商，请供应链负责人核对带时间的库存记录、供货协议或交期确认单。"),
    (r"兼容|适配|机型|能用|适用|支持|规格|尺寸|参数|功率|sku|货号|gtin|型号",
     "请补充商品编号和完整型号，请商品负责人提供对应版本的规格书、商品主数据或适配测试记录，再核实具体商品。"),
    (r"制度|流程|审批|报销|负责|归属|部门|组织|员工|休假|考勤|年假",
     "请说明适用部门和时间，请制度负责人提供现行制度、组织职责表或已批准的流程文件，再核对适用条款。"),
)


_ENTERPRISE_ONLY_HINT = re.compile(r"本店|我店|我司|本公司|我们公司|内部|员工|客户|会员|仓库")


def allows_general_knowledge_fallback(question: str) -> bool:
    """判定未命中内部资料的问题是否属于可交由通用模型回答的非企业专有事项。"""
    normalized = "".join(unicodedata.normalize("NFKC", question).casefold().split()).rstrip("。?!")
    if _ENTERPRISE_ONLY_HINT.search(normalized):
        return False
    return not any(re.search(pattern, normalized) for pattern, _ in _EVIDENCE_GUIDANCE)


def current_shanghai_clock() -> str:
    """返回东八区当前日期与时间，供通用问答时间校准使用。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S (Asia/Shanghai)")


def resolve_evidence_gap(question: str, *, allow_public_reference: bool) -> EvidenceGapAnswer:
    """仅为完整命中的通用定义题采用内置公开资料，其他请求返回待补凭据。"""
    normalized = "".join(unicodedata.normalize("NFKC", question).casefold().split()).rstrip("。?!")
    if allow_public_reference and (match := _DEFINITION_QUESTION.fullmatch(normalized)):
        term = match["object"] or match["term"] or (match["typed_term"] + match["kind"])
        for definition in _DEFINITIONS:
            if term in definition.terms:
                return {
                    "answer": definition.explanation,
                    "answer_basis": "public_reference",
                    "guidance": "这段释义整理自下列官方资料。可打开原文核对，本次未实时联网。",
                    "public_sources": [{"title": definition.source_title, "url": definition.source_url}],
                }
    guidance = next(
        (instruction for pattern, instruction in _EVIDENCE_GUIDANCE if re.search(pattern, normalized)),
        "请说明要核实的事项和适用范围，并请资料负责人提供已发布且您有权查看的文件，补齐依据后再提问。",
    )
    return {
        "answer": "当前没有足够的授权资料，暂时无法确认这一问题的结论。",
        "answer_basis": "insufficient_evidence",
        "guidance": guidance,
        "public_sources": [],
    }
