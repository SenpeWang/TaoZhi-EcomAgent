"""知识库问答领域核验规则：专家角色定义、逐字/省略号引文匹配与可溯源算式验证。"""
from __future__ import annotations

import ast
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Dict, Literal, Tuple

from taozhi.domain.entity_extraction import extract_entities

RetrievalDecision = Literal[
    "retry_weak_evidence", "retry_low_confidence", "sufficient_evidence",
    "run_specialists", "no_authorized_evidence", "external_processing_blocked", "no_finance_evidence",
]

SPECIALIST_AGENTS: Dict[str, Tuple[str, str]] = {
    "product": ("商品规格分析师", "核对型号、尺寸、材料、批次和参数，不混用不同型号。"),
    "compatibility": ("产品兼容性分析师", "死磕手机完整机型、屏幕形态、微缝公差与互斥死角，不推测兼容。"),
    "after_sales": ("售后政策顾问", "核对渠道、凭证、时间及售后条件，不承诺退款、赔付。"),
    "coach": ("产品培训顾问", "负责员工商品知识研习导学、设备操作规程带教与疑难排障教学。"),
    "operations": ("商品运营分析师", "核对商品卖点和选品依据，不捏造销量、转化率、排名。"),
    "supply": ("库存与供应分析师", "依据供应链资料核对起订条件和约定交期，不推测当前库存或预计到货时间。"),
    "finance": ("经营财务分析师", "核对财务依据，不推测利润、采购价或客户经营信息。"),
}

_NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)")
_VOLT_AMP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[Vv]\s*(\d+(?:\.\d+)?)\s*[Aa]")
_ARITH_UNIT_RE = re.compile(r"(?:个工作日|工作日|小时|元|块|折|成|天|台|件|%|W|w|g)")
_FINANCE_TERMS = re.compile("财务|经营|成本|毛利|利润|收入|结算|分摊|账期|补贴|对账")
_PHONE_MODEL = re.compile(r"(?:iphone|ipad|galaxy\s*[as]?|pixel|mate|pura|redmi|小米)\s*\d+(?:\s*(?:pro\s*max|pro\+?|plus|ultra|max|mini|se))?(?![a-z0-9])", re.I)
_FACT_SUBJECTS = {
    "退换与退款": r"退货|退款|换货|换新|退换|应退|无理由",
    "保修条件": r"保修|质保|保固",
    "价格": r"价格|售价|现价|报价|单价|多少钱",
    "库存": r"库存|现货|可售数量",
    "订单状态": r"订单状态|签收|物流状态",
    "发货与交期": r"发货|交期|交货|交付时间",
    "兼容性": r"兼容|适配|通用|适用机型|支持.{0,10}机型",
    "产品尺寸": r"切幅|幅宽|尺寸|长宽|厚度",
    "财务依据": r"成本|利润|毛利|分摊|账期|结算",
}
_QUESTION_FILLER = re.compile(r"请注明出处|请提供来源|请问|请告诉我|我们(?:公司|店铺)?|本(?:公司|店)|关于|多少|什么|怎么|是否|能否|可以|这个|这款|资料|依据|来源|出处")
_FORMULA = re.compile(r"([\d.()+*/×÷\s-]+)[＝=]\s*(\d+(?:\.\d+)?)\s*(%?)")
_CLAUSE_BOUNDARY = "。！？!?；;\n"
_CONDITION = re.compile(r"不|未|须|需|仅|限|但|若|除|如|条件|情况下|前提")
_SCOPE_NOTE = re.compile(r"^[（(【*\s]*(?:仅|只限|限于|限[定为]|例外|但|除外|除非|不含|不包括|适用范围|前提|附加条件)")
_CHANNELS = ((r"线上|网店|网上", r"线下|实体店|门店"), (r"直营|自营", r"第三方|经销商|代理商"))
_PERIOD = re.compile(r"(?<!\d)(?:19|20)\d{2}(?=\s*[年版])|(?<![a-z0-9])v\d+(?:\.\d+)*(?![a-z0-9.])", re.I)
_OUR_POLICY = re.compile(r"本店|我店|我们店|我司|本公司|我们公司|我们是否|我们承诺")
_OTHER_POLICY = re.compile(r"友商|竞品|其他(?:店|公司|品牌)|另一家公司|对方(?:公司|品牌)|第三方|品牌方|[\u4e00-\u9fffA-Za-z]{2,}品牌|原厂|制造商|供应商")
_SOURCE_ONLY = re.compile(r"(?:请(?:注明|提供|说明)|请问)?(?:出处|来源|引用|依据)(?:是什么|在哪里|在哪)?[？！?。]*")
_NUMBER = r"(?P<number>\d{1,8}(?:\.\d{1,4})?)"
_DURATION = _NUMBER + r"(?P<unit>个月|月|年|天)"
_WARRANTY = r"(?:保修|质保|保固)(?:期限|期|时长)?"
_RETURN_POLICY = r"(?P<window>(?:\d{1,3}|[一二三四五六七八九十]{1,3})天)?无理由退货"
_RETURN_DECISION = r"(?P<decision>不支持|不接受|不允许|不提供|支持|接受|允许|提供)"
_COMPARABLE_RULES = (
    ("保修期限", re.compile(r"(?:提供|享受)?" + _WARRANTY + r"(?:为|是|[：:])?" + _DURATION)),
    ("保修期限", re.compile(r"(?:提供|享受)?" + _DURATION + r"的?" + _WARRANTY)),
    ("切幅", re.compile(r"(?:切幅|裁切幅宽)(?:为|是|[：:])?" + _NUMBER + r"(?P<unit>毫米|厘米|mm|cm)", re.I)),
    ("无理由退货", re.compile(_RETURN_DECISION + _RETURN_POLICY)),
    ("无理由退货", re.compile(_RETURN_POLICY + r"[：:]?" + _RETURN_DECISION)),
)
_UNITS = {"月": ("月", 1), "个月": ("月", 1), "年": ("月", 12), "天": ("天", 1),
          "cm": ("毫米", 10), "厘米": ("毫米", 10), "mm": ("毫米", 1), "毫米": ("毫米", 1)}
_DOCUMENT_LABEL = re.compile(r"产品手册|商品手册|售后手册|产品规格|商品规格|规格[书表]|技术手册|售后政策|保修政策|退货政策|政策|说明书|产品说明|说明|手册|条款|资料")
_NON_EXCLUSIVE = re.compile(r"不少于|不超过|不低于|至少|至多|最多|最长|大约|约为|左右|以上|以下|任选|可选|分别|或者|[~～]|\d+(?:毫米|mm|月|天)?[至到～]\d+")
_QUALIFIER = re.compile(r"渠道|订单|批次|适用|拆封|会员|范围|系列|版本|凭证|发票|线上|线下|门店|经销|直营")


def _models(text: str) -> set[str]:
    family_models = _PHONE_MODEL.findall(text)
    product_codes = extract_entities(_PHONE_MODEL.sub(" ", text).upper())
    return {norm_text(item).upper() for item in [*product_codes, *family_models]}


def fact_subjects(text: str) -> set[str]:
    """识别有明确业务依据要求的子问，用于提示具体证据缺口。"""
    return {label for label, pattern in _FACT_SUBJECTS.items() if re.search(pattern, text)}


def _applicable_models(quote: str, document: str, title: str) -> set[str]:
    named = _models(quote)
    if named:
        return named
    scope = _models(title + "\n" + document)
    return scope if len(scope) == 1 else set()


def _matches_scope(question: str, quote: str, document: str, title: str) -> bool:
    if _OUR_POLICY.search(question) and _OTHER_POLICY.search(quote + title) and not _OUR_POLICY.search(quote + title):
        return False
    for dimension in _CHANNELS:
        wanted = {pattern for pattern in dimension if re.search(pattern, question)}
        if wanted:
            specified = {pattern for pattern in dimension if re.search(pattern, quote)}
            scope = specified or {pattern for pattern in dimension if re.search(pattern, title)}
            if not wanted <= scope and not re.search("全部渠道|所有渠道|全渠道|不限渠道", quote):
                return False
    requested = {part.upper() for part in _PERIOD.findall(question)}
    if not requested:
        return True
    periods = {part.upper() for part in (_PERIOD.findall(quote) or _PERIOD.findall(title))}
    if not periods:
        document_periods = set(_PERIOD.findall(document.upper()))
        if len(document_periods) == 1:
            periods = document_periods
    return requested <= periods


def _relevant_quote(question: str, quote: str, document: str, title: str) -> bool:
    models = _models(question)
    if models and models.isdisjoint(_applicable_models(quote, document, title)):
        return False
    if not _matches_scope(question, quote, document, title):
        return False
    subjects = fact_subjects(question)
    if subjects:
        return bool(subjects & fact_subjects(quote))
    words = _QUESTION_FILLER.sub(" ", question.casefold())
    words = _PHONE_MODEL.sub(" ", words)
    for code in extract_entities(words.upper()):
        words = words.replace(code.casefold(), " ")
    context = (title + " " + quote).casefold()
    identifiers = re.findall(r"[a-z][a-z\d]*(?:[-+][a-z\d]+)*", words)
    if re.search(r"什么是|是什么意思|的定义|的含义", question) or (identifiers and "是什么" in question):
        definition = r"(?:是(?:指|一[种项类个])|指的是|全称|简称|定义为)"
        if not re.search(definition, quote):
            return False
        definition_tail = r"\s*(?:接口|协议|连接器)?\s*(?:[（(][^）)]{1,40}[）)])?\s*的?" + definition
        if identifiers and not any(re.search(re.escape(term) + definition_tail, quote.casefold()) for term in identifiers):
            return False
    if identifiers:
        return any(re.search(r"(?<![a-z\d-])" + re.escape(identifier) + r"(?![a-z\d-])", context) for identifier in identifiers)
    terms = {word[i:i + 2] for word in re.findall(r"[\u4e00-\u9fff]+", words) for i in range(len(word) - 1)}
    if not terms:
        return bool(_SOURCE_ONLY.fullmatch(question.strip()) or (models and re.search(r"规格|参数|介绍|资料", question)))
    return sum(term in context for term in terms) >= (2 if len(terms) >= 5 else 1)


def document_addresses_question(question: str, document: str, title: str) -> bool:
    """人工核验的候选须至少有一处同时匹配主题和适用范围，避免跨行拼接机型和条款。"""
    return any(_relevant_quote(question, passage, document, title)
               for passage in re.split(r"[。！？；;\n]", document) if passage.strip())


def missing_models(question: str, evidence: list[dict], findings: list[dict]) -> list[str]:
    """多机型比较只计入确有已核验结论的机型。"""
    requested = _models(question)
    if len(requested) < 2:
        return []
    for finding in findings:
        source = evidence[finding["evidence_index"]]
        requested.difference_update(_applicable_models(finding["claim"], source["quote"], source["title"]))
    return sorted(requested)


def _scope_text(text: str) -> str:
    text = norm_text(_PHONE_MODEL.sub("", text)).upper()
    for model in extract_entities(text):
        text = text.replace(model, "")
    return text.strip("《》【】[]（）()：:的，,、-— ")


def _comparable_facts(document: str, title: str, document_id: str):
    """提取明确对象和限定条件下的单值断言，保留标题及原句中的差异。"""
    title_scope = _scope_text(_DOCUMENT_LABEL.sub("", title))
    sentences = [norm_text(part) for part in re.split(r"[。！？；;\n]", document) if part.strip()]
    heading = ""
    for position, sentence in enumerate(sentences):
        matches = [(label, match) for label, pattern in _COMPARABLE_RULES if (match := pattern.search(sentence))]
        if not matches:
            heading = sentence
            continue
        models = _models(sentence) or _models(heading) or _models(title) or _models(document)
        if len(models) > 1 or _NON_EXCLUSIVE.search(sentence):
            continue
        owner = next(iter(models), "") or ("企业政策" if _OUR_POLICY.search(title + heading + sentence) else title_scope or document_id)
        if not owner:
            continue
        following = sentences[position + 1] if position + 1 < len(sentences) else ""
        condition = following if _SCOPE_NOTE.match(following) or _QUALIFIER.search(following) else ""
        for label, match in matches:
            if sentence[match.end():].startswith(("起", "或", "至", "到", "-", "/", "／")):
                continue
            if label != "无理由退货" and sentence[:match.start()].endswith(("不", "无", "不再", "不能", "无法", "并非")):
                continue
            scope = tuple(sorted(filter(None, {title_scope, _scope_text(heading), _scope_text(condition),
                                               _scope_text(sentence[:match.start()] + sentence[match.end():])})))
            if label == "无理由退货":
                unit = (match["window"] or "").replace("七天", "7天").replace("十五天", "15天")
                value = not match["decision"].startswith("不")
            else:
                unit, multiplier = _UNITS[match["unit"].lower()]
                value = Decimal(match["number"]) * multiplier
            yield (owner, label, unit, scope), value, sentence


def conflicting_findings(evidence: Iterable[tuple[int, dict]], findings: list[dict]) -> tuple[list[dict], list[str]]:
    """对照全部授权原文，移除存在同范围反例的结论，指出供人工复核的原资料编号。"""
    if not findings:
        return [], []
    sources = defaultdict(lambda: defaultdict(list))
    for index, item in evidence:
        for key, value, sentence in _comparable_facts(item["quote"], item["title"], item.get("document_id", "")):
            sources[key][value].append((index, sentence))
    blocked = set()
    issues = []
    for (_, label, _, _), values in sources.items():
        if len(values) < 2:
            continue
        passages = {entry for entries in values.values() for entry in entries}
        affected = []
        for position, finding in enumerate(findings):
            claim = norm_text(finding["claim"]).strip("。！？；;")
            if any(i == finding["evidence_index"] and (sentence in claim or claim in sentence) for i, sentence in passages):
                affected.append(position)
        if affected:
            blocked.update(affected)
            refs = "、".join(f"[{i + 1}]" for i in sorted({i for i, _ in passages}))
            issues.append(f"资料 {refs} 对同一适用范围的{label}记载不一致，请人工核对生效版本和适用条款。")
    return [finding for i, finding in enumerate(findings) if i not in blocked], issues


def _whole_excerpt(excerpt: str, source: str) -> bool:
    """排除在句子中间截断的否定、限定条件和实体名称。"""
    boundaries = _CLAUSE_BOUNDARY if _CONDITION.search(source) else _CLAUSE_BOUNDARY + "，,"
    compact = re.sub(r"[^\S\n]+", "", source)
    needle = re.sub(r"[^\S\n]+", "", excerpt).strip().rstrip(_CLAUSE_BOUNDARY + "，,")
    if not needle:
        return False
    match = re.search(r"(?:^|(?<=[" + boundaries + r"]))" + re.escape(needle) + r"(?=$|[" + boundaries + "])", compact)
    if not match:
        return False
    line_start = compact.rfind("\n", 0, match.start()) + 1
    line_end = compact.find("\n", match.end())
    remainder = compact[line_start:match.start()] + compact[match.end():line_end if line_end >= 0 else len(compact)]
    previous_line = compact[:line_start].rstrip().split("\n")[-1]
    next_line = compact[line_end:].lstrip().split("\n", 1)[0] if line_end >= 0 else ""
    return not (_CONDITION.search(remainder) or _SCOPE_NOTE.match(previous_line) or _SCOPE_NOTE.match(next_line))


def is_finance_evidence(item: dict) -> bool:
    """判断授权片段是否包含财务或经营依据。"""
    return bool(_FINANCE_TERMS.search(item["title"] + item["quote"]))


def norm_text(s: object) -> str:
    """引用比对前的空白归一化：忽略换行、空格与全角空格差异。"""
    return "".join(str(s).split())


def cited_paragraphs(question: str, evidence: list[dict], findings: Iterable[dict]) -> Iterator[str]:
    """将已经核验并去重的结论渲染为带引文正文。"""
    include_title = bool(re.search(r"来源|出处|哪份|文档|条款|引用|依据", question))
    for position, finding in enumerate(findings):
        claim = finding["claim"]
        separator = "\n\n" if position else ""
        index = finding["evidence_index"]
        title = str(evidence[index].get("title") or "").strip()
        citation = f"（来源：《{title}》）[{index + 1}]" if include_title and title and title not in claim else f" [{index + 1}]"
        yield separator + claim + citation


def quote_supported(quote: str, doc_quote: str) -> bool:
    """校验引用片段是否受原文支撑；支持跨行空白归一化与省略号有序锚点匹配。"""
    q_norm = norm_text(quote)
    d_norm = norm_text(doc_quote)
    if not q_norm or not d_norm:
        return False
    if q_norm in d_norm:
        return True
    if "..." not in q_norm and "…" not in q_norm:
        return False
    segs = [s for s in re.split(r"(?:\.{3,}|…+)", q_norm) if s]
    if not segs or not any(len(s) >= 4 for s in segs):
        return False
    cursor = 0
    for seg in segs:
        pos = d_norm.find(seg, cursor)
        if pos < 0:
            return False
        cursor = pos + len(seg)
    return True


def _eval_arith_ast(node: ast.AST, grounded_vals: set[float]) -> float | None:
    """安全求值四则运算 AST：仅当所有字面量操作数均在已核验集合中时返回结果。"""
    if isinstance(node, ast.Expression):
        return _eval_arith_ast(node.body, grounded_vals)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        val = float(node.value)
        return val if any(abs(val - gv) < 1e-6 for gv in grounded_vals) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sub = _eval_arith_ast(node.operand, grounded_vals)
        if sub is None:
            return None
        return sub if isinstance(node.op, ast.UAdd) else -sub
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval_arith_ast(node.left, grounded_vals)
        right = _eval_arith_ast(node.right, grounded_vals)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return None if abs(right) < 1e-9 else left / right
    return None


def numbers_supported(claim: str, allowed_doc_text: str, *, question: str = "") -> bool:
    """数字与算式只在当前引用范围内核验，问题中的数字仅能用作运算输入。"""
    claim_nums = _NUM_RE.findall(claim)
    if not claim_nums:
        return True
    src_tokens = set(_NUM_RE.findall(allowed_doc_text))
    grounded_vals = {float(x) for x in src_tokens}
    for v_s, a_s in _VOLT_AMP_RE.findall(allowed_doc_text):
        v_val, a_val = float(v_s), float(a_s)
        grounded_vals.update({v_val, a_val, v_val * a_val})
    for a_s, b_s in _RATIO_RE.findall(allowed_doc_text):
        a_val, b_val = float(a_s), float(b_s)
        total = a_val + b_val
        if total > 0:
            grounded_vals.update(
                {total, a_val / total, b_val / total, 10.0 * a_val / total, 10.0 * b_val / total, 100.0 * a_val / total, 100.0 * b_val / total}
            )
    pure_formula = _FORMULA.fullmatch(claim) is not None
    eq_parts = re.split(r"[=＝]", claim)
    for idx in range(len(eq_parts) - 1):
        lhs_raw = eq_parts[idx]
        m_rhs = re.match(r"\s*(\d+(?:\.\d+)?)", eq_parts[idx + 1])
        if not m_rhs:
            continue
        expr = lhs_raw.replace("×", "*").replace("÷", "/").replace("（", "(").replace("）", ")")
        expr = "".join(_ARITH_UNIT_RE.sub("", expr).split())
        if not pure_formula:
            m_tail = re.search(r"([(\d][0-9+\-*/().]*)$", expr)
            if not m_tail:
                continue
            expr = m_tail.group(1)
        if not re.search(r"[+\-*/]", expr):
            continue
        try:
            tree = ast.parse(expr, mode="eval")
        except (SyntaxError, ValueError, RecursionError):
            return False
        operands = {float(tok) for tok in _NUM_RE.findall(expr)}
        if not operands.intersection(grounded_vals):
            return False
        val = _eval_arith_ast(tree, grounded_vals | {float(tok) for tok in _NUM_RE.findall(question)})
        if val is None:
            return False
        rhs_val = float(m_rhs.group(1))
        if eq_parts[idx + 1][m_rhs.end():].lstrip().startswith("%"):
            val *= 100.0
        if not math.isfinite(val) or not math.isfinite(rhs_val) or abs(val - rhs_val) >= 1e-6:
            return False
        grounded_vals.update(operands)
        grounded_vals.add(rhs_val)
    for tok in claim_nums:
        if tok in src_tokens:
            continue
        tv = float(tok)
        if not any(abs(tv - gv) < 1e-4 for gv in grounded_vals):
            return False
    return True


def supported_finding_text(question: str, claim: str, quote: str, document: str, title: str) -> str | None:
    """返回可发布的原文事实或带依据的明确验算，拒绝无法确定语义的模型改写。"""
    if not quote_supported(quote, document):
        return None
    if norm_text(claim).rstrip("。；;") in norm_text(quote) and _whole_excerpt(claim, document) and _relevant_quote(question, claim, document, title):
        return claim
    formula = claim.rstrip("。").strip()
    if len(formula) > 100 or not _FORMULA.fullmatch(formula) or not re.search(r"[+*/×÷-]", formula):
        return None
    if not _whole_excerpt(quote, document) or not _relevant_quote(question, quote, document, title):
        return None
    if not numbers_supported(formula, quote, question=question):
        return None
    return f"{quote}\n按题设验算：{formula}"
