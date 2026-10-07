"""通用数据模型：证据 / 引用 / 文档 / 切片 / 权限上下文。

设计原则（PRD N2/E3）：
* 每一条结论都必须携带 Evidence（文档名 + 页码 + 原文段落），缺失即触发重生成；
* 所有可检索对象都带 `owner_id / org_tag / is_public` 三维权限标签，实现"可见才可答"。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class DocType(str, Enum):
    """商品知识库文档类型。"""

    MANUAL = "manual"                  # 商品手册
    SPEC_SHEET = "spec_sheet"          # 参数表
    PRODUCT_PAGE = "product_page"      # 详情页
    TUTORIAL = "tutorial"              # 使用教程
    FAQ = "faq"                        # 售后FAQ
    POLICY = "policy"                  # 售后政策
    OTHER = "other"                    # 其他/未分类


class Sentiment(str, Enum):
    SUPPORT = "support"      # 支持该假设
    REFUTE = "refute"        # 反驳该假设
    NEUTRAL = "neutral"      # 中性/背景


class Evidence(BaseModel):
    """证据链最小单元：可溯源到原文段落。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("ev"))
    source_id: str = ""
    doc_name: str = ""
    page: Optional[int] = None
    section: str = ""
    quote: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    sentiment: Sentiment = Sentiment.NEUTRAL
    confidence: float = 0.5
    doc_type: DocType = DocType.OTHER
    score: float = 0.0
    # 权限标签：可见才可答
    owner_id: str = ""
    org_tag: str = ""
    is_public: bool = True

    @property
    def citation_text(self) -> str:
        page = f"P{self.page}" if self.page else "全文"
        return f"[{self.doc_name} {page}]"

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class SourceDocument(BaseModel):
    """采集到的原始文档。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("doc"))
    title: str = ""
    source: str = ""                      # 数据源适配器名称
    url: str = ""
    doc_type: DocType = DocType.OTHER
    published_at: Optional[datetime] = None
    raw_text: str = ""
    pages: List[Dict[str, Any]] = Field(default_factory=list)   # [{page, text, has_image}]
    images: List[str] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    tables_description: str = ""
    meta: Dict[str, Any] = Field(default_factory=dict)
    owner_id: str = ""
    org_tag: str = ""
    is_public: bool = True
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def doc_id(self) -> str:
        return self.id


class TextChunk(BaseModel):
    """按章节切块后的文本片段，是向量库 / BM25 的基本单位。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("chunk"))
    doc_id: str = ""
    doc_name: str = ""
    source: str = ""
    url: str = ""
    doc_type: DocType = DocType.OTHER
    section: str = ""
    page: Optional[int] = None
    text: str = ""
    order: int = 0
    owner_id: str = ""
    org_tag: str = ""
    is_public: bool = True
    meta: Dict[str, Any] = Field(default_factory=dict)

    def to_evidence(self, quote: str = "", sentiment: Sentiment = Sentiment.NEUTRAL,
                    confidence: float = 0.6, score: float = 0.0) -> Evidence:
        snippet = quote or self.text[:220]
        return Evidence(
            source_id=self.doc_id,
            doc_name=self.doc_name or self.source,
            page=self.page,
            section=self.section,
            quote=snippet,
            url=self.url,
            sentiment=sentiment,
            confidence=confidence,
            doc_type=self.doc_type,
            score=score,
            owner_id=self.owner_id,
            org_tag=self.org_tag,
            is_public=self.is_public,
        )


class PermissionContext(BaseModel):
    """权限收敛上下文：userId / orgTag / isPublic 三维过滤。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = ""
    org_tag: str = ""
    roles: List[str] = Field(default_factory=lambda: ["user"])
    is_admin: bool = False

    def visible(self, owner_id: str = "", org_tag: str = "", is_public: bool = True, doc_id: str = "") -> bool:
        if doc_id and not doc_id.startswith("business:"):
            from ..security.access import get_access_store
            store=get_access_store()
            record=store.get(doc_id)
            if record is not None:
                return store.allowed(record,self)
            from ..config import get_settings
            if get_settings().app_env.lower() in ("prod","production"):
                return False
        if self.is_admin:
            return True
        if org_tag and org_tag != self.org_tag:
            return False
        if set(self.roles) & {"admin","boss"}:
            return bool(org_tag and org_tag == self.org_tag) or (is_public and not org_tag)
        if is_public:
            return not org_tag or org_tag == self.org_tag
        return bool(owner_id and owner_id == self.user_id and org_tag == self.org_tag)

    def filter_expr(self) -> str:
        raise RuntimeError("旧三维权限表达式不支持文档授权，请使用当前权限注册表")


class Citation(BaseModel):
    """报告中的引用标注（支持点击跳转原文）。"""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    doc_name: str = ""
    page: Optional[int] = None
    quote: str = ""
    url: str = ""
    source: str = ""

    def render(self) -> str:
        page = f"P{self.page}" if self.page else "全文"
        return f"[{self.index}] {self.doc_name} {page}"
