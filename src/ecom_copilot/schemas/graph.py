"""商品知识图谱数据模型。

节点：Brand / Category / Product / Sku / Accessory / Issue
边：BELONGS_TO_BRAND / IN_CATEGORY / HAS_SKU / COMPATIBLE_WITH / SUPERSEDES / RELATES_TO_ISSUE
每条边强制携带 evidence + confidence，支持历史版本（valid_from / valid_to）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .common import Evidence, new_id


class NodeType(str, Enum):
    BRAND = "Brand"            # 品牌
    CATEGORY = "Category"      # 类目
    PRODUCT = "Product"        # 商品（型号）
    SKU = "Sku"                # SKU（颜色/套装等在售规格）
    ACCESSORY = "Accessory"    # 配件/耗材
    ISSUE = "Issue"            # 售后问题


class EdgeType(str, Enum):
    BELONGS_TO_BRAND = "BELONGS_TO_BRAND"    # 商品 → 品牌
    IN_CATEGORY = "IN_CATEGORY"              # 商品 → 类目
    HAS_SKU = "HAS_SKU"                      # 商品 → SKU
    COMPATIBLE_WITH = "COMPATIBLE_WITH"      # 配件 ↔ 商品/SKU 适配兼容
    SUPERSEDES = "SUPERSEDES"                # 新型号 升级替代 旧型号
    RELATES_TO_ISSUE = "RELATES_TO_ISSUE"    # 商品/配件 → 售后问题


class PriceBand(str, Enum):
    """商品价位段归类（替代原产业链环节 ChainStage）。"""

    ENTRY = "入门"
    MID = "主流"
    FLAGSHIP = "旗舰"
    UNKNOWN = "未分类"

    @classmethod
    def coerce(cls, value: Optional[str]) -> "PriceBand":
        if not value:
            return cls.UNKNOWN
        text = str(value).strip()
        for item in cls:
            if item.value == text:
                return item
        if "旗舰" in text or "高端" in text:
            return cls.FLAGSHIP
        if "主流" in text or "中端" in text:
            return cls.MID
        if "入门" in text or "基础" in text:
            return cls.ENTRY
        return cls.UNKNOWN


class GraphNode(BaseModel):
    """通用图谱节点。

    子类通过 `_prop_keys` 声明领域字段，构造时自动同步进 props（沿用原 CompanyNode 的做法），
    便于存储层统一按 props 读写。
    """

    model_config = ConfigDict(extra="forbid")

    _prop_keys: ClassVar[Tuple[str, ...]] = ()

    id: str = Field(default_factory=lambda: new_id("n"))
    type: NodeType = NodeType.PRODUCT
    name: str = ""
    props: Dict[str, Any] = Field(default_factory=dict)
    org_tag: str = ""
    owner_id: str = ""
    is_public: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def __init__(self, **data: Any):
        data.setdefault("props", {})
        for key in self._prop_keys:
            if key in data:
                value = data[key]
                data["props"][key] = value.value if isinstance(value, Enum) else value
        super().__init__(**data)

    @property
    def label(self) -> str:
        return self.type.value


class BrandNode(GraphNode):
    """品牌节点（如膜法工坊 MofaLab）。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = ("description", "slogan")

    type: NodeType = NodeType.BRAND
    name: str
    description: str = ""          # 品牌简介
    slogan: str = ""               # 品牌口号


class CategoryNode(GraphNode):
    """商品类目节点（如钢化膜 / 手机壳 / 膜切机 / UV打印机）。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = ("parent", "description")

    type: NodeType = NodeType.CATEGORY
    name: str
    parent: str = ""               # 上级类目
    description: str = ""          # 类目说明


class ProductNode(GraphNode):
    """商品（型号）节点：参数 / 价位段 / 卖点等商品事实。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = (
        "category", "price_band", "release_year", "positioning",
        "selling_points", "specs",
    )

    type: NodeType = NodeType.PRODUCT
    name: str
    category: str = ""                              # 商品类目
    price_band: PriceBand = PriceBand.UNKNOWN       # 价位段（入门/主流/旗舰）
    release_year: Optional[int] = None              # 上市年份
    positioning: str = ""                           # 市场定位描述
    selling_points: List[str] = Field(default_factory=list)   # 卖点
    specs: Dict[str, Any] = Field(default_factory=dict)       # 核心参数（切幅/防窥角度/打印精度等）


class SkuNode(GraphNode):
    """SKU 节点：同一商品下的颜色 / 套装等在售规格。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = ("sku_id", "color", "bundle", "price", "stock")

    type: NodeType = NodeType.SKU
    name: str
    sku_id: str = ""               # SKU 编码
    color: str = ""                # 颜色
    bundle: str = ""               # 套装/赠品组合
    price: str = ""                # 价格（文本口径）
    stock: str = ""                # 库存状态


class AccessoryNode(GraphNode):
    """配件/耗材节点（如贴膜神器 P1、清洁套装 K1、指环支架 G1）。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = ("part_no", "accessory_type", "fits_models", "consumable")

    type: NodeType = NodeType.ACCESSORY
    name: str
    part_no: str = ""                                # 物料编号（P1/K1/G1 等）
    accessory_type: str = ""                         # 配件类型（贴膜神器/清洁套装/指环支架…）
    fits_models: List[str] = Field(default_factory=list)      # 适配的商品型号
    consumable: bool = True                          # 是否耗材


class IssueNode(GraphNode):
    """售后问题节点：故障现象 + 解决方案摘要。"""

    _prop_keys: ClassVar[Tuple[str, ...]] = ("symptom", "solution_summary")

    type: NodeType = NodeType.ISSUE
    name: str
    symptom: str = ""              # 故障/问题现象
    solution_summary: str = ""     # 解决方案摘要


class GraphEdge(BaseModel):
    """带证据与版本的历史化关系边。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("e"))
    type: EdgeType
    source: str = ""                 # 源节点 name
    target: str = ""                 # 目标节点 name
    props: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_to: Optional[datetime] = None      # 旧版本保留，新版本 valid_to=None
    version: int = 1

    def key(self) -> str:
        return f"{self.type.value}|{self.source}|{self.target}"


class EntityMention(BaseModel):
    """知识抽取 Agent 产出的实体提及。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: NodeType = NodeType.PRODUCT
    price_band: PriceBand = PriceBand.UNKNOWN
    attributes: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = 0.6


class SupplyTriple(BaseModel):
    """知识抽取 Agent 产出的三元组（商品关系为主：适配/兼容/升级替代/故障关联）。"""

    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: EdgeType = EdgeType.COMPATIBLE_WITH
    obj: str
    subject_type: NodeType = NodeType.PRODUCT
    obj_type: NodeType = NodeType.PRODUCT
    props: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = 0.6

    def key(self) -> str:
        return f"{self.predicate.value}|{self.subject}|{self.obj}"


class ExtractionResult(BaseModel):
    """单次抽取结果（强 Schema 约束）。"""

    model_config = ConfigDict(extra="forbid")

    entities: List[EntityMention] = Field(default_factory=list)
    triples: List[SupplyTriple] = Field(default_factory=list)
    notes: str = ""


class GraphStats(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    by_node_type: Dict[str, int] = Field(default_factory=dict)
    by_edge_type: Dict[str, int] = Field(default_factory=dict)
    by_price_band: Dict[str, int] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GraphQueryResult(BaseModel):
    cypher: str = ""
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    backend: str = "networkx"
    elapsed_ms: float = 0.0
