"""数据源适配器实现（A1 商品详情/参数 · A2 评价工单 · A3 售后政策 · A4 手册教程 · A5 语料）。

设计：所有适配器均读取**企业真实数据**：
- A1~A4 读取企业级入库目录 `data/incoming/<tenant_code>/`（与生产入库链路
  upload → worker 解析 → 切片 → 向量化 → PG/ES 使用的是同一份真实资料）；
- A5 读取企业知识语料 `data/corpus/`。

已移除本地模拟 JSON（data/mock）与 LLM 合成兜底：不再产出 synthetic 文档。
目录缺失、读取失败或权限异常时返回空列表并记录告警日志，异常隔离不阻断主流程。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Settings, get_settings
from ..schemas.common import DocType, SourceDocument
from .base import FetchRequest, SourceAdapter
from .corpus import (
    _DOCX_SUFFIXES,
    _PDF_SUFFIXES,
    _TEXT_SUFFIXES,
    read_corpus_text as _read_corpus_text,
)

logger = logging.getLogger(__name__)

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


# ───────────────────────── 通用骨架：企业真实入库目录 ─────────────────────────


class _EnterpriseIngestAdapter(SourceAdapter):
    """企业真实入库资料适配器基类。

    数据源为企业级入库目录 `data/incoming/<tenant_code>/`，即生产入库链路
    实际消费的真实资料目录（如 products_official/、ledger_templates/ 等）。
    子类通过 `ingest_subdirs` 声明自己负责的子目录范围；未声明则纳入全部。
    不产出任何模拟 / 合成数据：读取失败仅记录日志并返回空列表。
    """

    ingest_subdirs: Tuple[str, ...] = ()
    _root_cache: Optional[Path] = None

    def _ingest_root(self) -> Path:
        """企业入库根目录：data/incoming/<tenant_code>/（租户目录不存在则回退到 incoming/）。"""
        if self._root_cache is not None:
            return self._root_cache
        base = self.settings.data_dir / "incoming"
        tenant = ""
        try:
            from ..enterprise.config import load_config

            tenant = load_config().tenant_code
        except Exception as exc:  # noqa: BLE001  企业配置不可用时不应中断本地取数
            logger.warning("未能读取企业租户配置，回退到入库根目录 %s: %s", base, exc)
        root = (base / tenant) if tenant and (base / tenant).exists() else base
        self._root_cache = root
        return root

    def _in_scope(self, path: Path, root: Path) -> bool:
        """按子目录判定该文档是否属于本适配器负责范围。"""
        if not self.ingest_subdirs:
            return True
        try:
            parts = {p.lower() for p in path.relative_to(root).parts[:-1]}
        except ValueError:
            return False
        return bool(parts & {s.lower() for s in self.ingest_subdirs})

    def fetch(self, request: FetchRequest) -> List[SourceDocument]:
        root = self._ingest_root()
        if not root.exists():
            logger.warning("企业入库目录不存在，数据源 %s 返回空: %s", self.name, root)
            return []
        terms = _query_terms(request)
        suffixes = _TEXT_SUFFIXES | _PDF_SUFFIXES
        docs: List[SourceDocument] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if not self._in_scope(path, root):
                continue
            text = _read_corpus_text(path)
            if not text.strip():
                continue
            if terms and not any(t in text or t in path.name for t in terms):
                continue
            docs.append(self._ingest_doc(path, text, root))
            if len(docs) >= request.limit:
                break
        if not docs:
            logger.info("数据源 %s 在 %s 下未命中查询词 %s", self.name, root, terms or "(空)")
        return docs[: request.limit]

    def _ingest_doc(self, path: Path, text: str, root: Path) -> SourceDocument:
        raise NotImplementedError


# ───────────────────────── A1 商品详情页 / 站内参数接口 ─────────────────────────


class ProductPageAdapter(_EnterpriseIngestAdapter):
    """商品详情页 / 站内参数接口适配器。

    读取企业入库目录中的商品资料（products_official / product / catalog / sku），
    产出 DocType=PRODUCT_PAGE（详情页）/ SPEC_SHEET（参数表）。
    """

    name = "product_page"
    label = "商品详情页 · 企业入库商品资料"
    doc_type = DocType.PRODUCT_PAGE
    priority = "P0"
    ingest_subdirs = ("products_official", "product", "catalog", "sku")

    def _ingest_doc(self, path: Path, text: str, root: Path) -> SourceDocument:
        title = self._title(path, text)
        return _doc(
            title,
            text,
            f"file://{path}",
            datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            doc_type=DocType.SPEC_SHEET if "参数" in title else DocType.PRODUCT_PAGE,
            source="enterprise_incoming",
            path=str(path),
        )

    @staticmethod
    def _title(path: Path, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:80]
        return path.stem.replace("_", " ")


# ───────────────────────── A2 用户评价 / 客服工单流 ─────────────────────────


class ReviewFeedAdapter(_EnterpriseIngestAdapter):
    """用户评价 / 客服工单流适配器（售后 FAQ 与真实使用痛点来源）。

    读取企业入库目录中的售后 / 评价类资料（after_sales / review），产出 DocType=FAQ。
    """

    name = "review_feed"
    label = "用户评价 · 客服工单流"
    doc_type = DocType.FAQ
    priority = "P1"
    ingest_subdirs = ("after_sales", "review", "reviews")

    def _ingest_doc(self, path: Path, text: str, root: Path) -> SourceDocument:
        return _doc(
            ProductPageAdapter._title(path, text),
            text,
            f"file://{path}",
            datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            doc_type=DocType.FAQ,
            source="enterprise_incoming",
            path=str(path),
        )


# ───────────────────────── A3 平台售后政策 / 质保规则公告 ─────────────────────────


class PolicyNoticeAdapter(_EnterpriseIngestAdapter):
    """平台售后政策 / 质保规则公告适配器。

    读取企业入库目录中的政策 / 公告类资料（policies / notices / policy），产出 DocType=POLICY。
    """

    name = "policy_notice"
    label = "平台售后政策 · 质保规则公告"
    doc_type = DocType.POLICY
    priority = "P0"
    ingest_subdirs = ("policies", "notices", "policy")

    def _ingest_doc(self, path: Path, text: str, root: Path) -> SourceDocument:
        return _doc(
            ProductPageAdapter._title(path, text),
            text,
            f"file://{path}",
            datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            doc_type=DocType.POLICY,
            source="enterprise_incoming",
            path=str(path),
        )


# ───────────────────────── A4 商品手册 / 使用教程 ─────────────────────────


class ManualSourceAdapter(_EnterpriseIngestAdapter):
    """商品手册 / 使用教程文档源适配器。

    读取企业入库目录中的手册 / 培训类资料（manuals / training），
    产出 DocType=MANUAL（手册）/ TUTORIAL（教程）。
    """

    name = "manual_source"
    label = "商品手册 · 使用教程"
    doc_type = DocType.MANUAL
    priority = "P0"
    ingest_subdirs = ("manuals", "training")

    def _ingest_doc(self, path: Path, text: str, root: Path) -> SourceDocument:
        title = ProductPageAdapter._title(path, text)
        return _doc(
            title,
            text,
            f"file://{path}",
            datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            doc_type=DocType.TUTORIAL if "教程" in title else DocType.MANUAL,
            source="enterprise_incoming",
            path=str(path),
        )


# ───────────────────────── A5 本地商品知识语料 ─────────────────────────


def _corpus_doc(path: Path, text: str) -> SourceDocument:
    return _doc(
        path.stem.replace("_", " "),
        text,
        f"file://{path}",
        datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
        doc_type=_corpus_doctype(path),
        path=str(path),
    )


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
            logger.warning("企业语料目录不存在，数据源 %s 返回空: %s", self.name, self.corpus_dir)
            return []
        query_terms = _query_terms(request)
        # 中文检索按 2-gram 片段匹配，避免长句式子问题无法命中
        fragments = {term[i:i + 2] for term in query_terms for i in range(len(term) - 1)}
        fragments |= set(query_terms)
        suffixes = _TEXT_SUFFIXES | _PDF_SUFFIXES
        docs: List[SourceDocument] = []
        for path in sorted(self.corpus_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            text = _read_corpus_text(path)
            if fragments and not any(f in text or f in path.name for f in fragments):
                continue
            docs.append(_corpus_doc(path, text))
            if len(docs) >= request.limit:
                break
        if not docs:
            # 语料规模有限且经人工筛选，无强命中时整体返回，保证检索层有可用语料
            for path in sorted(self.corpus_dir.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                docs.append(_corpus_doc(path, _read_corpus_text(path)))
                if len(docs) >= request.limit:
                    break
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
    ]
    return [a for a in adapters if a.enabled]


def adapters_by_name(names: List[str], settings: Optional[Settings] = None) -> List[SourceAdapter]:
    registry = {a.name: a for a in default_adapters(settings)}
    picked = [registry[n] for n in names if n in registry]
    return picked or default_adapters(settings)
