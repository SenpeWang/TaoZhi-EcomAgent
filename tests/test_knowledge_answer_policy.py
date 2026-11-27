"""知识库无依据时，公开术语释义与企业业务事实的回答边界。"""
from urllib.parse import urlparse

import pytest

from taozhi.domain.knowledge_answer import resolve_evidence_gap


@pytest.mark.parametrize(("question", "fact", "publisher"), [
    ("请问，USB-C 是什么接口？", "正反插", "www.usb.org"),
    ("介绍一下 USB PD", "协商", "www.usb.org"),
    ("ＵＳＢ　ＰＤ 是什么协议？", "协商", "www.usb.org"),
    ("低功耗蓝牙指什么？", "广播", "www.bluetooth.com"),
    ("什么是 GTIN？", "贸易项目", "support.gs1.org"),
    ("SKU 的定义是什么？", "货号", "help.shopify.com"),
])
def test_standalone_definition_has_attributable_public_source(question, fact, publisher):
    """验证独立概念问题返回可归属的公开来源与联网说明。

    Args:
        question: ``question`` 参数化取值。
        fact: ``fact`` 参数化取值。
        publisher: ``publisher`` 参数化取值。
    """
    result = resolve_evidence_gap(question, allow_public_reference=True)
    assert result["answer_basis"] == "public_reference"
    assert fact in result["answer"] and "未实时联网" in result["guidance"]
    source, = result["public_sources"]
    link = urlparse(source["url"])
    assert source["title"] and link.scheme == "https" and link.hostname == publisher


@pytest.mark.parametrize(("question", "required_detail"), [
    ("什么是 USB PD？我们这款充电器支持吗？", "完整型号"),
    ("什么是 GTIN，我们店 A100 型号具体的 GTIN 是什么？", "商品编号"),
    ("请联网确认本店保修多久", "当期生效"),
    ("USB-C 的最新售价是多少？", "有效价目表"),
    ("这批货的交期？", "交期确认单"),
    ("昨天的结算利润是多少？", "财务负责人"),
    ("订单 123 的状态怎么样？", "订单编号"),
    ("本部门报销的审批人是谁？", "制度负责人"),
    ("公司规定里说的 USB PD 是什么意思？", "资料负责人"),
    ("它的定义是什么？", "适用范围"),
    ("什么是还没收录的通用术语？", "资料负责人"),
    ("SKU 是什么协议？", "商品负责人"),
])
def test_concrete_mixed_or_unknown_request_gets_evidence_guidance(question, required_detail):
    """验证混入本店细节或未知事实的问题退回资料不足并给出所需凭证。

    Args:
        question: ``question`` 参数化取值。
        required_detail: ``required_detail`` 参数化取值。
    """
    result = resolve_evidence_gap(question, allow_public_reference=True)
    assert result["answer_basis"] == "insufficient_evidence"
    assert "没有足够的授权资料" in result["answer"] and required_detail in result["guidance"]
    assert result["public_sources"] == [] and "http" not in result["answer"]


def test_internal_only_policy_takes_precedence_over_a_matching_definition():
    """验证仅限内部资料时，即使存在公开定义也不引用外部来源。"""
    result = resolve_evidence_gap("什么是 USB PD？", allow_public_reference=False)
    assert result["answer_basis"] == "insufficient_evidence" and not result["public_sources"]
