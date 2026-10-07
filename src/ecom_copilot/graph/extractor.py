"""知识抽取 Agent 的核心能力：品牌 / 商品 / SKU / 配件 / 售后问题及关系三元组抽取。"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..llm import ModelTier, get_llm
from ..llm.json_utils import dump, extract_json
from ..observability import inc, timer
from ..schemas.common import Evidence, Sentiment, TextChunk
from ..schemas.graph import (
    EdgeType,
    EntityMention,
    ExtractionResult,
    NodeType,
    PriceBand,
    SupplyTriple,
)

_EXTRACT_PROMPT = """你是电商商品知识抽取引擎。请从下列商品资料片段中抽取商品领域实体与关系。

查询主题：{question}
文本片段：
{context}

抽取规则：
1. entities：品牌（Brand，如 膜法工坊 MofaLab）、类目（Category，如 钢化膜/手机壳/膜切机/UV打印机）、商品（Product，具体型号如 防窥膜C3、膜切机MC-500）、
   SKU（Sku，按适配机型区分的在售规格）、配件（Accessory，贴膜神器/清洁套装/指环支架等耗材与配件）、
   售后问题（Issue，故障现象或售后政策条目，如"钢化膜起泡""7天无理由退换"）；
   每个实体给出 name、type、price_band（入门/主流/旗舰/未分类，仅商品需要）、attributes（类目/参数/卖点/适配型号/故障现象等）。
2. triples：只抽取文本中明确或强暗示的关系，谓词取值：
   BELONGS_TO_BRAND（商品属于品牌）、IN_CATEGORY（商品属于类目）、HAS_SKU（商品有SKU）、
   COMPATIBLE_WITH（配件适配/兼容/通用于商品或SKU）、SUPERSEDES（新型号升级替代旧型号）、
   RELATES_TO_ISSUE（商品/配件关联售后问题）。
3. 重点抽取 COMPATIBLE_WITH（适配/兼容/通用/适用于/贴/支持机型）、HAS_SKU、SUPERSEDES（升级替代/升级款/升级版）
   与 RELATES_TO_ISSUE（故障关联）；方向：膜/壳/配件 → 机型、新型号 → 旧型号、商品 → 售后问题。
4. 每条 triples 必须给出 evidence：来源文档名 doc_name、页码 page、原文依据 quote（不超过 60 字）。
5. confidence 取值 0-1，仅当文本直接支持时 ≥0.7；不要臆造文本中不存在的关系；不确定时 confidence 低于 0.5。

输出 JSON：{{"entities":[{{"name":"","type":"Product","price_band":"主流","attributes":{{}},"confidence":0.8}}],
"triples":[{{"subject":"","predicate":"COMPATIBLE_WITH","obj":"","subject_type":"Accessory","obj_type":"Product",
"props":{{}},"confidence":0.8,"evidence":[{{"doc_name":"","page":1,"quote":""}}]}}],"notes":""}}
最多 {max_items} 个实体、{max_items} 条关系。"""

# 商品型号 / 配件物料的静态词表（默认示例品牌：膜法工坊 MofaLab 手机配件）
_KNOWN_PRODUCT_MODELS = {
    "C1", "C2", "C3", "C5", "L1",                # 钢化膜 / 镜头膜
    "S10", "T20", "A30",                         # 手机壳
    "MC-300", "MC-500",                          # 膜切机
    "UV-600", "UV-900",                          # UV 打印机
}
_KNOWN_ACCESSORY_MODELS = {
    "P1": "贴膜神器P1", "K1": "清洁套装K1", "G1": "指环支架G1",
}
_KNOWN_MODELS = _KNOWN_PRODUCT_MODELS | set(_KNOWN_ACCESSORY_MODELS)
_KNOWN_COMPACT = {re.sub(r"\s+", "", m) for m in _KNOWN_MODELS}

# 配件前缀（中文别名 + 物料编号），用于识别"防窥膜C3""贴膜神器 P1"等写法
_ACC_PREFIX = r"(?:贴膜神器|清洁套装|指环支架|镜头膜|陶瓷膜|防窥膜|磨砂膜|高清膜|钢化膜|气囊防摔壳|液态硅胶壳|透明壳|手机壳|膜切机|UV打印机)"
_CODE = r"(?:UV\s?-?900|UV\s?-?600|MC\s?-?500|MC\s?-?300|[A-Z]{1,2}\s?\d{1,2}(?:\s*(?:Pro|Ultra))?)"
_REF = rf"(?:{_ACC_PREFIX})?\s*{_CODE}"

# 关系正则：适配 / 兼容 / 通用 / 适用于 / 可选配 / 升级替代
_REL_PATTERNS: List[Tuple[str, str, EdgeType]] = [
    (rf"({_REF})\s*(?:适配|适用于|兼容|通用(?:于)?|可用于)\s*({_REF})",
     "A 适配/兼容 B", EdgeType.COMPATIBLE_WITH),
    (rf"({_REF})[^。；\n]{{0,10}}?(?:可选配|可搭配|选配|搭配使用)\s*({_REF})",
     "A 可选配 B", EdgeType.COMPATIBLE_WITH),
    (rf"({_REF})\s*(?:与|和)\s*({_REF})[^。；\n]{{0,8}}?通用",
     "A 与 B 通用", EdgeType.COMPATIBLE_WITH),
    (rf"({_REF})[^。；\n]{{0,6}}?(?:全系|全系列)\s*(钢化膜|手机膜|手机壳|膜切机|UV打印机)\s*(?:通用|适用)",
     "A 全系通用", EdgeType.COMPATIBLE_WITH),
    (rf"({_REF})\s*(?:升级替代|升级换代|取代|替代|接替)\s*({_REF})",
     "A 升级替代 B", EdgeType.SUPERSEDES),
    (rf"({_REF})\s*是\s*({_REF})\s*(?:的)?\s*(?:升级款|升级版|换代产品|后续型号)",
     "A 是 B 的升级款", EdgeType.SUPERSEDES),
]

# 商品型号识别：配件前缀 + 编号，或裸编号（MC-500 / S10 / 防窥膜 C3 / 贴膜神器 P1 等）
_MODEL_RE = re.compile(rf"{_REF}")

_PRICE_BAND_HINTS = {
    PriceBand.FLAGSHIP: ("旗舰", "高端", "顶配", "双刀头", "专业"),
    PriceBand.MID: ("主流", "中端", "热销", "走量", "畅销"),
    PriceBand.ENTRY: ("入门", "基础款", "入门级", "性价比"),
}

_CATEGORY_WORDS = ("钢化膜", "手机膜", "手机壳", "膜切机", "切膜机", "UV打印机",
                   "手机配件", "打印设备")
_ACCESSORY_WORDS = ("贴膜神器", "清洁套装", "指环支架", "支架", "配件", "耗材")
_ISSUE_WORDS = ("故障", "起泡", "堵头", "切歪", "碎边", "发黄", "脱胶", "蓝牙",
                "售后", "保修", "质保", "退换", "维修", "以旧换新")


def _canonical_model(raw: str) -> str:
    """把匹配到的型号片段规范化为词表内的标准名称（如 'MC 500'→'MC-500'，'P1'→'贴膜神器P1'）。"""
    text = re.sub(r"\s+", "", raw)
    hyphenless = text.replace("-", "")
    for model in _KNOWN_MODELS:
        standard = re.sub(r"\s+", "", model)
        if text == standard or hyphenless == standard.replace("-", ""):
            return model
    if text in _KNOWN_ACCESSORY_MODELS:
        return _KNOWN_ACCESSORY_MODELS[text]
    for prefix in ("贴膜神器", "清洁套装", "指环支架", "镜头膜", "陶瓷膜", "防窥膜",
                   "磨砂膜", "高清膜", "钢化膜", "气囊防摔壳", "液态硅胶壳", "透明壳",
                   "手机壳", "膜切机", "UV打印机"):
        if text.startswith(prefix):
            return prefix + text[len(prefix):]
    return text


def _guess_price_band(text: str) -> PriceBand:
    for band, hints in _PRICE_BAND_HINTS.items():
        if any(h in text for h in hints):
            return band
    return PriceBand.UNKNOWN


def _guess_node_type(name: str) -> NodeType:
    text = name or ""
    if "品牌" in text or "mofalab" in text.lower() or "膜法工坊" in text:
        return NodeType.BRAND
    if any(w in text for w in _CATEGORY_WORDS):
        return NodeType.CATEGORY
    if "sku" in text.lower():
        return NodeType.SKU
    if any(w in text for w in _ACCESSORY_WORDS):
        return NodeType.ACCESSORY
    if any(w in text for w in _ISSUE_WORDS):
        return NodeType.ISSUE
    return NodeType.PRODUCT


class KnowledgeExtractor:
    """LLM 抽取 + 规则兜底的混合抽取器（商品领域）。"""

    def __init__(self, max_items: int = 12) -> None:
        self.max_items = max_items

    def extract(self, chunks: List[TextChunk], question: str = "") -> ExtractionResult:
        if not chunks:
            return ExtractionResult()
        result = ExtractionResult()
        llm_result = self._llm_extract(chunks, question)
        if llm_result:
            result = llm_result
        rule_entities, rule_triples = self._rule_extract(chunks)
        # 合并：LLM 结果为主，规则补充
        known_entities = {e.name for e in result.entities}
        for entity in rule_entities:
            if entity.name not in known_entities:
                result.entities.append(entity)
                known_entities.add(entity.name)
        known_triples = {t.key() for t in result.triples}
        for triple in rule_triples:
            if triple.key() not in known_triples:
                result.triples.append(triple)
                known_triples.add(triple.key())
        inc("extract.entities", len(result.entities))
        inc("extract.triples", len(result.triples))
        return result

    def _llm_extract(self, chunks: List[TextChunk], question: str) -> Optional[ExtractionResult]:
        if not get_llm().settings.has_llm:
            return None
        context = "\n\n".join(
            f"[{i + 1}] 文档：{c.doc_name}（P{c.page or '-'}）\n{c.text[:700]}"
            for i, c in enumerate(chunks[:10])
        )
        prompt = _EXTRACT_PROMPT.format(
            question=question or "商品知识问答",
            context=context[:7000],
            max_items=self.max_items,
        )
        with timer("extract.llm"):
            try:
                # 推理型模型需要更大的 token 预算，避免思维链耗尽预算导致正文为空
                raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=16000)
            except Exception:  # noqa: BLE001
                return None
        data = extract_json(raw, expect="object")
        if not isinstance(data, dict):
            return None
        entities: List[EntityMention] = []
        for item in (data.get("entities") or [])[: self.max_items]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if not _is_valid_entity_name(str(item["name"])):
                continue
            band = PriceBand.coerce(item.get("price_band"))
            if band == PriceBand.UNKNOWN:
                band = _guess_price_band(str(item.get("attributes", "")))
            entities.append(
                EntityMention(
                    name=str(item["name"]).strip(),
                    type=_coerce_node_type(item.get("type")),
                    price_band=band,
                    attributes=item.get("attributes") if isinstance(item.get("attributes"), dict) else {},
                    confidence=float(item.get("confidence", 0.6) or 0.6),
                )
            )
        triples: List[SupplyTriple] = []
        for item in (data.get("triples") or [])[: self.max_items]:
            if not isinstance(item, dict) or not (item.get("subject") and item.get("obj")):
                continue
            if not (_is_valid_entity_name(str(item["subject"]))
                    and _is_valid_entity_name(str(item["obj"]))):
                continue
            evidence = _build_evidence(item.get("evidence"), chunks)
            triples.append(
                SupplyTriple(
                    subject=str(item["subject"]).strip(),
                    predicate=_coerce_edge_type(item.get("predicate")),
                    obj=str(item["obj"]).strip(),
                    subject_type=_coerce_node_type(item.get("subject_type")),
                    obj_type=_coerce_node_type(item.get("obj_type")),
                    props=item.get("props") if isinstance(item.get("props"), dict) else {},
                    evidence=evidence,
                    confidence=float(item.get("confidence", 0.6) or 0.6),
                )
            )
        if not entities and not triples:
            return None
        return ExtractionResult(entities=entities, triples=triples,
                                notes=str(data.get("notes", ""))[:300])

    def _rule_extract(self, chunks: List[TextChunk]):
        entities: List[EntityMention] = []
        triples: List[SupplyTriple] = []
        seen_entities = set()
        seen_triples = set()
        for chunk in chunks:
            text = chunk.text
            for match in _MODEL_RE.finditer(text):
                name = _canonical_model(match.group(0))
                # 噪声过滤：不在词表、无中文前缀且过短的纯编号（如 A4）不算商品实体
                compact = re.sub(r"\s+", "", match.group(0))
                if (compact not in _KNOWN_COMPACT and len(compact) < 3
                        and not re.search(r"[\u4e00-\u9fa5]", compact)):
                    continue
                if len(name) < 2 or name in seen_entities or not _is_valid_entity_name(name):
                    continue
                seen_entities.add(name)
                entities.append(
                    EntityMention(
                        name=name,
                        type=_guess_node_type(name),
                        price_band=_guess_price_band(text[:200]),
                        attributes={"source": chunk.doc_name},
                        evidence=[chunk.to_evidence(quote=(text[:120]), confidence=0.5)],
                        confidence=0.5,
                    )
                )
            for pattern, _label, edge_type in _REL_PATTERNS:
                for subject, obj in re.findall(pattern, text):
                    subject = _canonical_model(subject)
                    obj = _canonical_model(obj)
                    key = f"{edge_type.value}|{subject}|{obj}"
                    if key in seen_triples:
                        continue
                    seen_triples.add(key)
                    triples.append(
                        SupplyTriple(
                            subject=subject, predicate=edge_type, obj=obj,
                            subject_type=_guess_node_type(subject),
                            obj_type=_guess_node_type(obj),
                            evidence=[chunk.to_evidence(quote=text[:200], confidence=0.55)],
                            confidence=0.55,
                        )
                    )
        return entities[: self.max_items], triples[: self.max_items]


def _build_evidence(raw: object, chunks: List[TextChunk]) -> List[Evidence]:
    """把 LLM 返回的证据挂到真实 chunk 上，缺失则按文档名回填（保证可溯源）。"""
    result: List[Evidence] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            doc_name = str(item.get("doc_name", "")).strip()
            quote = str(item.get("quote", "")).strip()
            page = item.get("page")
            matched = _match_chunk(chunks, doc_name, quote)
            if matched is not None:
                result.append(
                    matched.to_evidence(
                        quote=quote or matched.text[:180], confidence=0.7, score=0.7
                    )
                )
            elif doc_name or quote:
                result.append(
                    Evidence(
                        doc_name=doc_name or (chunks[0].doc_name if chunks else ""),
                        page=int(page) if isinstance(page, int) else None,
                        quote=quote,
                        confidence=0.5,
                        sentiment=Sentiment.NEUTRAL,
                    )
                )
    if not result and chunks:
        result = [chunks[0].to_evidence(confidence=0.5)]
    return result[:3]


def _match_chunk(chunks: List[TextChunk], doc_name: str, quote: str) -> Optional[TextChunk]:
    if quote:
        core = quote[:12]
        for chunk in chunks:
            if core and core in chunk.text:
                return chunk
    if doc_name:
        for chunk in chunks:
            if doc_name and doc_name in chunk.doc_name:
                return chunk
    return chunks[0] if chunks else None


_BAD_NAME_TOKENS = ("超过", "远超", "约", "以上", "以下", "达到", "同比", "环比", "为全球", "合计")


def _is_valid_entity_name(name: str) -> bool:
    """过滤抽取噪声：过长片段、含统计量词的伪实体。"""
    name = (name or "").strip()
    if not (2 <= len(name) <= 24):
        return False
    if any(tok in name for tok in _BAD_NAME_TOKENS):
        return False
    if any(ch in name for ch in "。，；：！？、（）()【】"):
        return False
    return True


def _coerce_node_type(value: object) -> NodeType:
    text = str(value or "").strip().lower()
    for node_type in NodeType:
        if node_type.value.lower() == text:
            return node_type
    if "brand" in text or "品牌" in text:
        return NodeType.BRAND
    if "categ" in text or "类目" in text:
        return NodeType.CATEGORY
    if "sku" in text:
        return NodeType.SKU
    if "access" in text or "配件" in text or "耗材" in text:
        return NodeType.ACCESSORY
    if "issue" in text or "售后" in text or "故障" in text:
        return NodeType.ISSUE
    return NodeType.PRODUCT


def _coerce_edge_type(value: object) -> EdgeType:
    text = str(value or "").strip().upper()
    for edge_type in EdgeType:
        if edge_type.value == text:
            return edge_type
    if "COMPAT" in text or "适配" in text or "兼容" in text:
        return EdgeType.COMPATIBLE_WITH
    if "SUPERSEDE" in text or "升级" in text or "替代" in text:
        return EdgeType.SUPERSEDES
    if "SKU" in text:
        return EdgeType.HAS_SKU
    if "BRAND" in text:
        return EdgeType.BELONGS_TO_BRAND
    if "CATEG" in text:
        return EdgeType.IN_CATEGORY
    if "ISSUE" in text or "RELATES" in text:
        return EdgeType.RELATES_TO_ISSUE
    return EdgeType.COMPATIBLE_WITH
