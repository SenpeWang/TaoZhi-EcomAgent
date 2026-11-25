"""验证知识库结论与对应授权引文、机型、限定条件及算式的绑定。"""

import pytest

from taozhi.application.rag import publication, specialists
from taozhi.domain.rag_verification import supported_finding_text


@pytest.mark.parametrize(("question", "claim", "quote", "document"), [
    ("本店提供终身保修吗？", "本店提供终身保修。", "本款为黑色。", "本款为黑色。"),
    ("支持退款吗？", "支持退款。", "支持退款。", "商品拆封后不支持退款。"),
    ("支持退款吗？", "可申请退款。", "可申请退款。", "仅在出示购货凭证后，可申请退款。"),
    ("本店提供保修吗？", "提供保修。", "提供保修。", "提供保修。但仅限企业采购渠道。"),
    ("本店提供保修吗？", "提供保修。", "提供保修。", "提供保修。\n仅限凭发票申请的企业客户。"),
    ("有保修吗？", "提供保修。", "提供保修。", "适用范围：仅限凭证申请。\n提供保修。"),
    ("线下门店保修多久？", "仅线上订单保修一年。", "仅线上订单保修一年。", "仅线上订单保修一年。"),
    ("直营渠道是否接受退货？", "第三方经销商接受退货。", "第三方经销商接受退货。", "第三方经销商接受退货。"),
    ("本店保修几年？", "某某品牌承诺终身保修。", "某某品牌承诺终身保修。", "某某品牌承诺终身保修。"),
    ("2026 年的保修期？", "2025 年保修一年。", "2025 年保修一年。", "2025 年保修一年。"),
    ("按 V3.2 制度保修多久？", "V3.1 规定保修一年。", "V3.1 规定保修一年。", "V3.1 规定保修一年。"),
    ("这？", "提供保修。", "提供保修。", "提供保修。"),
    ("A1 的颜色？", "A1 使用双接口。", "A1 使用双接口。", "A1 使用双接口。"),
    ("A1 是否适配？", "B2 适配。", "B2 适配。", "B2 适配。"),
    ("A1 保修多久？", "保修一年。", "保修一年。", "A1 和 B2 售后处理说明。保修一年。"),
    ("iPhone 15 Pro Max 保修多久？", "iPhone 15 Pro 保修一年。", "iPhone 15 Pro 保修一年。", "iPhone 15 Pro 保修一年。"),
    ("Galaxy S24 Ultra 保修多久？", "Galaxy S24 保修一年。", "Galaxy S24 保修一年。", "Galaxy S24 保修一年。"),
    ("什么是 USB-C？", "USB-A 是接口。", "USB-A 是接口。", "USB-A 是接口。"),
    ("USB-C 是什么？", "请定期检查 USB-C 接口。", "请定期检查 USB-C 接口。", "请定期检查 USB-C 接口。"),
    ("USB-C 是什么？", "USB-C 充电线是一种配件。", "USB-C 充电线是一种配件。", "USB-C 充电线是一种配件。"),
    ("本店保修是否为 30 天？", "保修为 30 天。", "商品可申请维修。", "商品可申请维修。其他商品的保修为 30 天。"),
    ("库存多少？", "毛利为百分之五。", "毛利为百分之五。", "毛利为百分之五。"),
    ("299 元退货应退多少？", "299 - 39 - 12 = 248", "退货需扣运费 12 元。", "退货需扣运费 12 元。"),
    ("299 元退货应退多少？", "299 - 12 = 12", "退货需扣运费 12 元。", "退货需扣运费 12 元。"),
])
def test_unsubstantiated_finding_is_rejected(question, claim, quote, document):
    assert supported_finding_text(question, claim, quote, document, "商品资料") is None


def test_calculation_names_its_source_and_input_instead_of_promising_a_refund():
    quote = "退货需扣除运费 12 元及赠品折价 39 元。"
    formula = "299 - 39 - 12 = 248"
    answer = supported_finding_text("客户下单 299 元，退货应退多少？", formula, quote, quote, "售后政策")
    assert answer == quote + "\n按题设验算：" + formula
    assert supported_finding_text("客户提交退货申请", formula, quote, quote, "售后政策") is None


def test_whole_context_can_support_a_definition_or_conditional_policy():
    definition = "USB-C 是一种可以正反插的连接器。"
    assert supported_finding_text("USB-C 是什么？", definition, definition, definition, "术语") == definition
    policy = "提供保修。但仅限企业采购渠道。"
    assert supported_finding_text("本店提供保修吗？", policy, policy, policy, "售后") == policy
    dated = "2026 年直营门店提供一年保修。"
    assert supported_finding_text("2026 年线下门店如何保修？", dated, dated, dated, "售后") == dated


def test_quality_exposes_only_cited_facts_and_flags_the_unanswered_subject(monkeypatch):
    quote = "MC-500 切幅为 120mm。"
    evidence = [{"title": "规格表", "quote": quote}, {"title": "产品手册", "quote": quote}]
    def finding(index, claim):
        return {"role": "product", "evidence_index": index, "claim": claim, "quote": quote}
    state = dict(question="MC-500 和 MC-501 的切幅和保修期是多少？", evidence=evidence, findings=[
        finding(0, quote), finding(1, quote), finding(1, "保修期 120 天。"), finding(-1, quote),
    ], job_id="job", run_version=1, hard_block=False, external_blocked=False, sources=[])
    monkeypatch.setattr(specialists, "validate_task_execution", lambda *_: (None, {"payload": {}}))
    streamed = []
    monkeypatch.setattr(specialists, "publish_answer_delta", lambda job, version, text, sources: streamed.append(text))
    output = specialists.verify_claims(state)
    assert output["findings"] == [finding(0, quote)]
    assert "缺少可核验的保修条件资料。" in output["missing"]
    assert "缺少机型 MC-501 的对应依据。" in output["missing"]
    assert streamed == [quote + " [1]"]


def published_question(monkeypatch, question, documents, chosen):
    """驱动质检和发布节点，捕获前端可以读取的全部流式正文。"""
    evidence = [
        dict(title=title, quote=quote, document_id=f"doc{i}", chunk_id=f"chunk{i}", level=1, version=1, page=1)
        for i, (title, quote) in enumerate(documents)
    ]
    state = dict(question=question, evidence=evidence, findings=[
        dict(role="product", evidence_index=i, quote=evidence[i]["quote"], claim=evidence[i]["quote"]) for i in chosen
    ], job_id="job", run_version=1, sources=[], hard_block=False, external_blocked=False,
        selected_agents=["product"], model_calls=1, usage=[], business=[], missing=[])
    for module in (specialists, publication):
        monkeypatch.setattr(module, "validate_task_execution", lambda *_: (None, {"payload": {}, "input_level": 1}))
    streamed = []
    monkeypatch.setattr(specialists, "publish_answer_delta", lambda job, version, text, sources: streamed.append(text))
    state.update(specialists.verify_claims(state))
    state.update(publication.compose_answer(state))
    return state, "".join(streamed)


@pytest.mark.parametrize(("question", "docs", "chosen"), [
    ("A1 的保修期限是多久？", [("产品手册", "A1 保修期限为 12 个月。"), ("售后政策", "A1 质保期为 24 个月。")], [0]),
    ("A1 的保修期限是多久？", [("产品手册", "A1 享受保修期为 12 个月。"), ("售后政策", "A1 质保期为 24 个月。")], [0]),
    ("MC-500 切幅是多少？", [("规格表", "MC-500 切幅为 100mm。"), ("产品手册", "MC-500 切幅为 120 毫米。")], [0, 1]),
    ("本店能否无理由退货？", [("售后政策", "本店支持七天无理由退货。"), ("售后手册", "本店不支持7天无理由退货。")], [1, 0]),
    ("A1 切幅是多少？", [("A1 规格表", "切幅为 100mm。"), ("A1 产品手册", "切幅为 120mm。")], [0]),
])
def test_conflicting_internal_policy_is_withheld_before_streaming(monkeypatch, question, docs, chosen):
    result, streamed = published_question(monkeypatch, question, docs, chosen)
    assert streamed == ""
    assert result["answer_basis"] == "insufficient_evidence"
    assert any("资料 [1]、[2]" in issue and "人工核对" in issue for issue in result["missing"])
    assert [item["document_id"] for item in result["evidence"]] == ["doc0", "doc1"]
    assert not result["findings"] and not result["excerpt_indices"]
    assert all(quote not in result["answer"] for _, quote in docs)


@pytest.mark.parametrize(("question", "docs"), [
    ("A1 和 B2 的切幅？", [("规格表", "A1 切幅为 100mm。"), ("规格表", "B2 切幅为 120mm。")]),
    ("A1 的保修期？", [("线上保修政策", "A1 保修期为12个月。"), ("线下保修政策", "A1 保修期为24个月。")]),
    ("A1 的保修期？", [("保修政策", "A1 保修期为12个月。但仅限线下订单。"), ("保修政策", "A1 保修期为24个月。但仅限线上订单。")]),
    ("A1 的保修期？", [("售后政策", "拆封商品。A1 保修期为12个月。"), ("售后手册", "未拆封商品。A1 保修期为24个月。")]),
    ("A1 的保修期？", [("售后政策", "A1 保修期为12个月。直营渠道。"), ("售后手册", "A1 保修期为24个月。经销渠道。")]),
    ("A1 的保修期？", [("售后政策", "A1 至少保修12个月。"), ("售后手册", "A1 至少保修24个月。")]),
    ("本店支持无理由退货吗？", [("售后政策", "未拆封时本店支持七天无理由退货。"), ("售后政策", "拆封后本店不支持七天无理由退货。")]),
    ("A1 切幅是多少？", [("规格表", "A1 切幅为 12cm。"), ("规格表", "A1 切幅为 120 毫米。")]),
    ("A1 的保修期限是多久？", [("售后政策", "A1 保修期为1年。"), ("产品手册", "A1 质保期为12个月。")]),
])
def test_different_scopes_and_equivalent_values_remain_publishable(monkeypatch, question, docs):
    result, streamed = published_question(monkeypatch, question, docs, [0, 1])
    assert result["answer_basis"] == "internal_evidence"
    assert all(quote in streamed and quote in result["answer"] for _, quote in docs)
    assert not result["missing"]


def test_conflict_does_not_hide_an_independently_supported_fact(monkeypatch):
    docs = [("售后政策", "A1 保修期12个月。"), ("产品手册", "A1 保修期24个月。"), ("规格表", "A1 切幅100mm。")]
    result, streamed = published_question(monkeypatch, "A1 的保修期和切幅？", docs, [0, 1, 2])
    assert streamed == result["answer"] == "A1 切幅100mm。 [3]"
    assert [item["evidence_index"] for item in result["findings"]] == [2]
    assert any("保修期限记载不一致" in issue for issue in result["missing"])
