"""数据源适配器实现（电商商品知识模块：A1 商品详情页/参数 / A2 评价工单 / A3 售后政策 / A4 手册教程）。

设计：电商数据源优先读取本地模拟数据（离线可用），其次 LLM 合成兜底；
两者都不可用时返回空列表降级，流程继续依赖本地语料等其他数据源
（与原公网采集「失败即返回空」的异常隔离风格一致，PRD 八、风险对策）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings, get_settings
from ..llm import ModelTier, get_llm
from ..llm.json_utils import extract_json
from ..schemas.common import DocType, SourceDocument
from .base import FetchRequest, SourceAdapter

_TEXT_SUFFIXES = {".md", ".txt", ".markdown"}
_PDF_SUFFIXES = {".pdf"}


def _doc(title: str, text: str, url: str = "", published: str = "",
         doc_type: DocType = DocType.OTHER, **meta: Any) -> SourceDocument:
    published_at = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y年%m月%d日"):
        try:
            published_at = datetime.strptime(published[:19], fmt)
            break
        except Exception:  # noqa: BLE001
            continue
    return SourceDocument(
        title=title, url=url, raw_text=text, published_at=published_at,
        doc_type=doc_type, meta=meta,
    )


def _query_terms(request: FetchRequest) -> List[str]:
    """查询词：关键词 + 商品类目 / 产品线 / 品牌。"""
    return [t for t in list(request.keywords)
            + [request.category, request.product_line, request.brand] if t]


def _match_item(item: Dict[str, Any], terms: List[str]) -> bool:
    if not terms:
        return True
    hay = " ".join(str(item.get(k, "")) for k in
                   ("title", "text", "brand", "category", "product_line", "product", "issuer"))
    return any(t in hay for t in terms)


# ───────────────────────── 通用骨架：模拟 JSON → LLM 合成 ─────────────────────────


class _MockJsonSourceAdapter(SourceAdapter):
    """电商数据源通用骨架：本地模拟 JSON → LLM 合成 → 空列表降级。

    模拟 JSON 约定存放在 `data/mock/<mock_file>`，格式为
    {"items": [{"title": "", "text": "", ...}]}；条目字段见各适配器 `_mock_doc`。
    无网络 / 无 LLM Key 时：读到模拟 JSON 即可正常工作；否则返回空列表，
    不抛出异常（与原公网爬虫的错误处理风格一致）。
    """

    mock_file: str = ""
    synth_prompt: str = ""
    synth_n: int = 4

    def fetch(self, request: FetchRequest) -> List[SourceDocument]:
        docs = self._from_mock(request)
        if not docs:
            docs = self._synthesize(request)
        return docs[: request.limit]

    # ───── 本地模拟 JSON ─────
    def _from_mock(self, request: FetchRequest) -> List[SourceDocument]:
        path = self.settings.data_dir / "mock" / self.mock_file
        if not self.mock_file or not path.exists():
            return []
        try:
            items = (json.loads(path.read_text(encoding="utf-8")) or {}).get("items") or []
        except Exception:  # noqa: BLE001
            return []
        terms = _query_terms(request)
        picked = [it for it in items if isinstance(it, dict) and _match_item(it, terms)]
        if not picked and items:
            # 模拟语料规模有限，无强命中时整体返回，保证问答链路可用
            picked = [it for it in items if isinstance(it, dict)]
        return [self._mock_doc(it) for it in picked[: request.limit]]

    def _mock_doc(self, item: Dict[str, Any]) -> SourceDocument:
        raise NotImplementedError

    # ───── LLM 合成兜底 ─────
    def _synthesize(self, request: FetchRequest) -> List[SourceDocument]:
        if not self.synth_prompt or not self.settings.synthetic_fallback_enabled \
                or not self.settings.has_llm:
            return []
        prompt = self.synth_prompt.format(
            n=self.synth_n,
            question=" / ".join(request.keywords) or request.category
            or request.product_line or "电商商品知识",
            brand=request.brand or "未指定",
            category=request.category or "未指定",
            product_line=request.product_line or "未指定",
        )
        try:
            raw = get_llm().chat(prompt, tier=ModelTier.FAST)
            items = (extract_json(raw, expect="object") or {}).get("items") or []
        except Exception:  # noqa: BLE001
            return []
        docs: List[SourceDocument] = []
        for item in items[: self.synth_n]:
            if isinstance(item, dict) and (item.get("title") or item.get("text")):
                docs.append(self._mock_doc(item))
        return docs


# ───────────────────────── A1 商品详情页 / 站内参数接口 ─────────────────────────


class ProductPageAdapter(_MockJsonSourceAdapter):
    """商品详情页 / 站内参数接口适配器。

    真实部署时对接电商平台站内商品接口；开发与离线环境优先读取
    `data/mock/product_pages.json` 模拟返回，其次由 LLM 合成。
    产出 DocType=PRODUCT_PAGE（详情页）/ SPEC_SHEET（参数表）。
    """

    name = "product_page"
    label = "商品详情页 · 站内参数接口"
    doc_type = DocType.PRODUCT_PAGE
    priority = "P0"
    mock_file = "product_pages.json"

    PROMPT = """你是手机配件商品知识资料撰写专家。请围绕问答主题生成 {n} 条「商品详情页 / 参数表」模拟资料，
用于在没有站内商品接口时驱动知识库构建与商品问答检索。

问答主题：{question}
品牌：{brand}    商品类目：{category}    产品线：{product_line}

要求：
- 每条包含：商品型号 / 名称、核心参数（如切幅、防窥角度、喷头精度、适配机型，以官方参数表口径为准）、卖点描述。
- 参数口径必须与官方参数表一致，不得编造精确参数；无把握时用「约」或省略。
- 输出 JSON：{{"items":[{{"title":"","kind":"详情页或参数表","text":"","published":"YYYY-MM-DD"}}]}}
"""

    def _mock_doc(self, item: Dict[str, Any]) -> SourceDocument:
        kind = str(item.get("kind", ""))
        return _doc(
            str(item.get("title", "")),
            str(item.get("text", "")),
            str(item.get("url", "")),
            str(item.get("published", "")),
            doc_type=DocType.SPEC_SHEET if "参数" in kind else DocType.PRODUCT_PAGE,
            kind=kind,
            brand=str(item.get("brand", "")),
            category=str(item.get("category", "")),
            product_line=str(item.get("product_line", "")),
            synthetic=True,
        )


# ───────────────────────── A2 用户评价 / 客服工单流 ─────────────────────────


class ReviewFeedAdapter(_MockJsonSourceAdapter):
    """用户评价 / 客服工单流适配器（售后 FAQ 与真实使用痛点来源）。

    优先读取 `data/mock/review_feed.json` 模拟评价与工单流，
    其次由 LLM 合成，产出 DocType=FAQ。
    """

    name = "review_feed"
    label = "用户评价 · 客服工单流"
    doc_type = DocType.FAQ
    priority = "P1"
    mock_file = "review_feed.json"

    PROMPT = """你是手机配件商品知识资料撰写专家。请围绕问答主题生成 {n} 条「用户评价 / 客服工单」模拟记录，
用于提炼售后 FAQ 与真实使用痛点。

问答主题：{question}
品牌：{brand}    商品类目：{category}    产品线：{product_line}

要求：
- 每条包含：涉及商品型号、问题描述（如钢化膜起泡 / UV 打印机堵头 / 切膜机切歪 / 切膜机连不上蓝牙等常见故障）、客服答复口径。
- 答复口径必须与官方售后政策一致，不得承诺保修范围之外的内容。
- 输出 JSON：{{"items":[{{"title":"","text":"","published":"YYYY-MM-DD"}}]}}
"""

    def _mock_doc(self, item: Dict[str, Any]) -> SourceDocument:
        return _doc(
            str(item.get("title", "")),
            str(item.get("text", "")),
            str(item.get("url", "")),
            str(item.get("published", "")),
            doc_type=DocType.FAQ,
            product=str(item.get("product", "")),
            synthetic=True,
        )


# ───────────────────────── A3 平台售后政策 / 质保规则公告 ─────────────────────────


class PolicyNoticeAdapter(_MockJsonSourceAdapter):
    """平台售后政策 / 质保规则公告适配器。

    优先读取 `data/mock/policy_notices.json` 模拟平台公告，
    其次由 LLM 合成，产出 DocType=POLICY。
    """

    name = "policy_notice"
    label = "平台售后政策 · 质保规则公告"
    doc_type = DocType.POLICY
    priority = "P0"
    mock_file = "policy_notices.json"

    PROMPT = """你是手机配件商品知识资料撰写专家。请围绕问答主题生成 {n} 条「平台售后政策 / 质保规则」模拟公告，
用于支撑售后政策类问答。

问答主题：{question}
品牌：{brand}    商品类目：{category}

要求：
- 每条包含：政策标题、适用商品范围、核心条款（如 7 天无理由 / 设备与喷头质保期 / 耗材是否保修 / 以旧换新）、发布主体。
- 条款口径必须与官方售后政策一致，不得自行扩展质保承诺。
- 输出 JSON：{{"items":[{{"title":"","text":"","issuer":"","published":"YYYY-MM-DD"}}]}}
"""

    def _mock_doc(self, item: Dict[str, Any]) -> SourceDocument:
        return _doc(
            str(item.get("title", "")),
            str(item.get("text", "")),
            str(item.get("url", "")),
            str(item.get("published", "")),
            doc_type=DocType.POLICY,
            issuer=str(item.get("issuer", "")),
            synthetic=True,
        )


# ───────────────────────── A4 商品手册 / 使用教程 ─────────────────────────


class ManualSourceAdapter(_MockJsonSourceAdapter):
    """商品手册 / 使用教程文档源适配器。

    优先读取 `data/mock/manuals.json` 模拟手册电子稿，
    其次由 LLM 合成；产出 DocType=MANUAL（手册）/ TUTORIAL（教程）。
    """

    name = "manual_source"
    label = "商品手册 · 使用教程"
    doc_type = DocType.MANUAL
    priority = "P0"
    mock_file = "manuals.json"

    PROMPT = """你是电商商品知识资料撰写专家。请围绕问答主题生成 {n} 段「商品手册 / 使用教程」模拟文档，
用于在没有手册电子稿时驱动知识库构建。

问答主题：{question}
品牌：{brand}    产品线：{product_line}    商品类目：{category}

要求：
- 每段包含：适用型号、章节主题（如上手设置 / 日常维护 / 配件安装与型号适配 / 故障排查）、操作步骤。
- 型号与配件的适配关系、参数口径必须与官方参数表一致。
- 输出 JSON：{{"items":[{{"title":"","kind":"手册或教程","text":"","published":"YYYY-MM-DD"}}]}}
"""

    def _mock_doc(self, item: Dict[str, Any]) -> SourceDocument:
        kind = str(item.get("kind", ""))
        return _doc(
            str(item.get("title", "")),
            str(item.get("text", "")),
            str(item.get("url", "")),
            str(item.get("published", "")),
            doc_type=DocType.TUTORIAL if "教程" in kind else DocType.MANUAL,
            kind=kind,
            product_line=str(item.get("product_line", "")),
            synthetic=True,
        )


# ───────────────────────── A5 本地商品知识语料 ─────────────────────────


def _read_corpus_text(path: Path) -> str:
    """读取语料文本：md/txt 直接读取；pdf 尽力用 pypdf 抽取（未安装则留空）。"""
    if path.suffix.lower() in _PDF_SUFFIXES:
        try:
            from pypdf import PdfReader  # noqa: PLC0415

            return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
        except Exception:  # noqa: BLE001
            return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _corpus_doctype(path: Path) -> DocType:
    """推断语料 DocType（供融合排序的文档类型权重使用）。

    优先按相对 data/corpus/ 的子目录映射（product/compatibility/quality → 参数表口径，
    sku → 参数表（价格/政策 → 政策口径），manuals → 教程（话术 → 手册），
    after_sales → FAQ（政策细则/规范 → 政策口径），notices → 政策口径，training → 手册）；
    子目录外（如 data/corpus/documents/）回退按文件名关键词推断。
    """
    name = path.stem
    lowered = name.lower()
    sub = ""
    for parent in path.parents:
        if parent.name == "corpus":
            rel = path.relative_to(parent)
            if len(rel.parts) > 1:
                sub = rel.parts[0]
            break
    if sub == "product" or sub == "compatibility" or sub == "quality":
        return DocType.SPEC_SHEET
    if sub == "sku":
        return DocType.POLICY if ("价格" in name or "政策" in name) else DocType.SPEC_SHEET
    if sub == "manuals":
        return DocType.MANUAL if "话术" in name else DocType.TUTORIAL
    if sub == "after_sales":
        return DocType.POLICY if ("政策细则" in name or "规范" in name) else DocType.FAQ
    if sub == "notices":
        return DocType.POLICY
    if sub == "training":
        return DocType.MANUAL
    # 子目录外兜底：按文件名关键词推断
    if "参数" in name:
        return DocType.SPEC_SHEET
    if "教程" in name:
        return DocType.TUTORIAL
    if "faq" in lowered:
        return DocType.FAQ
    if "政策" in name or "保修" in name:
        return DocType.POLICY
    if "详情" in name:
        return DocType.PRODUCT_PAGE
    return DocType.MANUAL


def _corpus_doc(path: Path, text: str) -> SourceDocument:
    return _doc(
        path.stem.replace("_", " "),
        text,
        f"file://{path}",
        datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
        doc_type=_corpus_doctype(path),
        path=str(path),
    )


class LocalCorpusAdapter(SourceAdapter):
    """本地商品知识语料库（data/corpus 下商品手册 / 参数表 / SKU 与配件指南 /
    使用教程 / 售后 FAQ / 保修政策等 md 文档，及 data/corpus/documents 下的 PDF）。"""

    name = "local_corpus"
    label = "本地商品知识库语料"
    doc_type = DocType.MANUAL
    priority = "P0"

    def __init__(self, settings: Optional[Settings] = None,
                 corpus_dir: Optional[Path] = None) -> None:
        super().__init__(settings)
        self.corpus_dir = Path(corpus_dir) if corpus_dir else self.settings.data_dir / "corpus"

    def fetch(self, request: FetchRequest) -> List[SourceDocument]:
        if not self.corpus_dir.exists():
            return []
        query_terms = _query_terms(request)
        # 中文检索按 2-gram 片段匹配，避免长句式子问题无法命中
        fragments = {term[i:i + 2] for term in query_terms for i in range(len(term) - 1)}
        fragments |= set(query_terms)
        suffixes = _TEXT_SUFFIXES | _PDF_SUFFIXES
        docs: List[SourceDocument] = []
        for path in sorted(self.corpus_dir.rglob("*")):
            if path.suffix.lower() not in suffixes:
                continue
            text = _read_corpus_text(path)
            if fragments and not any(f in text or f in path.name for f in fragments):
                continue
            docs.append(_corpus_doc(path, text))
            if len(docs) >= request.limit:
                break
        if not docs:
            # 本地语料规模有限且经人工筛选，无强命中时整体返回，保证检索层有可用语料
            for path in sorted(self.corpus_dir.rglob("*")):
                if path.suffix.lower() not in suffixes:
                    continue
                docs.append(_corpus_doc(path, _read_corpus_text(path)))
                if len(docs) >= request.limit:
                    break
        return docs


# ───────────────────────── A6 兜底：LLM 合成数据 ─────────────────────────


class SyntheticCorpusAdapter(SourceAdapter):
    """LLM 生成模拟数据兜底（仅在数据源全部失效时启用）。

    产出文档会被标记 `synthetic=true`，回答中会以「模拟数据」显著标注，
    避免与真实引用混淆（PRD 八、风险对策 / E3 强制溯源）。
    """

    name = "synthetic"
    label = "LLM 合成兜底语料"
    doc_type = DocType.OTHER   # 合成标记统一走 meta.synthetic=true
    priority = "P2"
    read_only = True

    PROMPT = """你是一位电商商品知识资料撰写专家。请针对问答主题生成 {n} 条"商品知识速记"，
用于在没有外部数据源时驱动商品适配图谱与商品问答推理。

问答主题：{question}
关注产品线：{focus}
品牌：{brand}    商品类目：{category}

要求：
- 每条包含：来源名称、核心事实（商品型号与参数、配件型号适配关系或售后条款）、时间。
- 内容须符合电商商品知识常识，口径必须与官方参数表一致；不要编造精确参数，如需数量请用"约"。
- 输出 JSON：{{"items":[{{"title":"","source":"","text":"","published":"YYYY-MM-DD"}}]}}
"""

    def __init__(self, settings: Optional[Settings] = None, n: int = 4) -> None:
        super().__init__(settings)
        self.n = n

    def fetch(self, request: FetchRequest) -> List[SourceDocument]:
        if not self.settings.synthetic_fallback_enabled or not self.settings.has_llm:
            return []
        prompt = self.PROMPT.format(
            n=self.n,
            question=" / ".join(request.keywords) or request.category,
            focus=request.product_line or "全产品线",
            brand=request.brand or "未指定",
            category=request.category or "未指定",
        )
        try:
            raw = get_llm().chat(prompt, tier=ModelTier.FAST)
            data = extract_json(raw, expect="object") or {}
            items = data.get("items") or []
        except Exception:  # noqa: BLE001
            return []
        docs: List[SourceDocument] = []
        for item in items[: self.n]:
            title = str(item.get("title", "")).strip()
            text = str(item.get("text", "")).strip()
            if not title and not text:
                continue
            docs.append(
                _doc(
                    title or "合成商品知识速记",
                    text,
                    "",
                    str(item.get("published", ""))[:10],
                    org=item.get("source", ""),
                    synthetic=True,
                )
            )
        return docs


# ───────────────────────── 注册表 ─────────────────────────


def default_adapters(settings: Optional[Settings] = None) -> List[SourceAdapter]:
    cfg = settings or get_settings()
    adapters: List[SourceAdapter] = [
        LocalCorpusAdapter(cfg),
        ProductPageAdapter(cfg),
        ReviewFeedAdapter(cfg),
        PolicyNoticeAdapter(cfg),
        ManualSourceAdapter(cfg),
        SyntheticCorpusAdapter(cfg),
    ]
    return [a for a in adapters if a.enabled]


def adapters_by_name(names: List[str], settings: Optional[Settings] = None) -> List[SourceAdapter]:
    registry = {a.name: a for a in default_adapters(settings)}
    picked = [registry[n] for n in names if n in registry]
    return picked or default_adapters(settings)
