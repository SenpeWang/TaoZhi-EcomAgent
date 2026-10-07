"""商品知识图谱构建与动态更新（适配关系 / 升级替代链 / 类目聚合 / 适配缺口分析）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Settings, get_settings
from ..llm import embed_texts
from ..observability import inc, timer
from ..schemas.graph import (
    EdgeType,
    ExtractionResult,
    GraphEdge,
    GraphNode,
    GraphStats,
    NodeType,
    PriceBand,
)
from .store import GraphStore, get_graph_store

# 常用配件类型关键词，用于适配覆盖缺口分析
_ACCESSORY_KINDS = ("钢化膜", "手机壳", "镜头膜", "贴膜神器", "清洁套装", "指环支架")


class IndustryGraphBuilder:
    """把商品抽取结果写入图库，支持增量更新与历史版本保留。"""

    def __init__(self, store: Optional[GraphStore] = None,
                 settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.store = store or get_graph_store(self.settings)

    def build(self, extraction: ExtractionResult) -> Dict[str, Any]:
        new_nodes = 0
        new_edges = 0
        updated_edges = 0
        with timer("graph.build"):
            for entity in extraction.entities:
                node = GraphNode(
                    type=entity.type,
                    name=entity.name,
                    props={
                        "price_band": entity.price_band.value,
                        **{k: v for k, v in entity.attributes.items()},
                        "confidence": entity.confidence,
                    },
                    org_tag=entity.evidence[0].org_tag if entity.evidence else "",
                    owner_id=entity.evidence[0].owner_id if entity.evidence else "",
                )
                before = self.store.get_node(entity.name)
                self.store.upsert_node(node)
                if before is None:
                    new_nodes += 1

            for triple in extraction.triples:
                if triple.confidence < 0.35:
                    continue
                edge = GraphEdge(
                    type=triple.predicate,
                    source=triple.subject,
                    target=triple.obj,
                    props={**triple.props, "confidence": triple.confidence},
                    evidence=triple.evidence,
                    confidence=triple.confidence,
                )
                existed = self.store.get_node(triple.subject) is not None and any(
                    e.key() == edge.key() for e in self.store.edges(edge.type)
                )
                self.store.upsert_edge(edge)
                if existed:
                    updated_edges += 1
                else:
                    new_edges += 1

        if isinstance(self.store, GraphStore) and hasattr(self.store, "save"):
            try:
                self.store.save()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        inc("graph.nodes.new", new_nodes)
        inc("graph.edges.new", new_edges)
        return {
            "new_nodes": new_nodes,
            "new_edges": new_edges,
            "updated_edges": updated_edges,
            "stats": self.store.stats().model_dump(mode="json"),
        }

    def stats(self) -> GraphStats:
        return self.store.stats()

    def vis_payload(self, limit: int = 300) -> Dict[str, Any]:
        return self.store.to_vis_payload(limit) if hasattr(self.store, "to_vis_payload") else {}

    # ── 跨图谱推理（商品适配 / 升级替代） ──

    def compatible_accessories(self, product: str, depth: int = 1) -> List[Dict[str, Any]]:
        """查询机型/商品的兼容膜壳配件（COMPATIBLE_WITH 双向邻接），如「iPhone 16 Pro Max 的兼容膜/壳」。"""
        return self.store.neighbors(product, [EdgeType.COMPATIBLE_WITH], "both", depth)

    def supersede_chain(self, product: str, depth: int = 3) -> Dict[str, Any]:
        """查询商品的升级替代链： newer SUPERSEDES older。"""
        newer: List[Dict[str, Any]] = []
        older: List[Dict[str, Any]] = []
        frontier = [product]
        seen = {product}
        for _ in range(max(1, depth)):
            next_frontier: List[str] = []
            for name in frontier:
                for item in self.store.neighbors(name, [EdgeType.SUPERSEDES], "out", 1):
                    target = item.get("target")
                    if target and target not in seen:
                        seen.add(target)
                        newer.append({"model": target, "supersedes": name,
                                      "confidence": item.get("confidence", 0.0)})
                        next_frontier.append(target)
                for item in self.store.neighbors(name, [EdgeType.SUPERSEDES], "in", 1):
                    source = item.get("source")
                    if source and source not in seen:
                        seen.add(source)
                        older.append({"model": source, "superseded_by": name,
                                      "confidence": item.get("confidence", 0.0)})
                        next_frontier.append(source)
            frontier = next_frontier
        return {"target": product, "superseded_by": newer, "supersedes": older}

    def category_overview(self, category: str = "") -> Dict[str, Any]:
        """类目聚合：统计类目下商品、SKU、配件数量与价位段分布。"""
        products: List[Dict[str, Any]] = []
        for node in self.store.nodes(NodeType.PRODUCT):
            node_category = str(node.props.get("category", ""))
            if category and category not in node_category:
                continue
            products.append({
                "name": node.name,
                "category": node_category,
                "price_band": node.props.get("price_band", PriceBand.UNKNOWN.value),
                "selling_points": node.props.get("selling_points", []),
            })
        band_dist: Dict[str, int] = {}
        for p in products:
            band = p["price_band"]
            band_dist[band] = band_dist.get(band, 0) + 1
        return {
            "category": category or "全部类目",
            "product_count": len(products),
            "products": products[:50],
            "price_band_distribution": band_dist,
            "sku_count": len(self.store.nodes(NodeType.SKU)),
            "accessory_count": len(self.store.nodes(NodeType.ACCESSORY)),
            "issue_count": len(self.store.nodes(NodeType.ISSUE)),
        }

    def compatibility_gaps(self, category: str = "") -> Dict[str, Any]:
        """适配覆盖缺口分析：找出缺少常用配件适配的商品，以及没有挂靠任何商品的孤儿配件。"""
        products = [n for n in self.store.nodes(NodeType.PRODUCT)
                    if (not category) or category in str(n.props.get("category", ""))]
        accessories = self.store.nodes(NodeType.ACCESSORY)
        # accessory -> 适配的商品集合
        fit_map: Dict[str, set] = {a.name: set() for a in accessories}
        for edge in self.store.edges(EdgeType.COMPATIBLE_WITH):
            if edge.source in fit_map:
                fit_map[edge.source].add(edge.target)
            if edge.target in fit_map:
                fit_map[edge.target].add(edge.source)

        gaps: List[Dict[str, Any]] = []
        for product in products:
            covered = {a for a, fits in fit_map.items() if product.name in fits}
            covered_kinds = set()
            for acc in covered:
                for kind in _ACCESSORY_KINDS:
                    if kind in acc:
                        covered_kinds.add(kind)
            missing = [kind for kind in _ACCESSORY_KINDS if kind not in covered_kinds]
            if missing:
                gaps.append({
                    "product": product.name,
                    "category": product.props.get("category", ""),
                    "covered_accessories": sorted(covered)[:10],
                    "missing_kinds": missing,
                })
        orphans = [a for a, fits in fit_map.items() if not fits]
        return {"category": category, "gaps": gaps[:50], "orphan_accessories": orphans[:50]}

    def gap_analysis(self, category: str = "", price_band: Optional[PriceBand] = None) -> Dict[str, Any]:
        """适配覆盖缺口识别（沿用旧公开函数名，语义已商品化：见 compatibility_gaps）。"""
        result = self.compatibility_gaps(category)
        if price_band is not None:
            result["gaps"] = [
                g for g in result["gaps"]
                if self.store.get_node(g["product"]) is not None
                and self.store.get_node(g["product"]).props.get("price_band") == price_band.value
            ]
        return result

    def index_embeddings(self) -> int:
        """为商品/配件描述建立向量索引（图查询 + 向量检索混合召回的基础）。"""
        nodes = self.store.nodes(NodeType.PRODUCT) + self.store.nodes(NodeType.ACCESSORY)
        if not nodes:
            return 0
        texts = [
            f"{n.name} {n.props.get('category', '')} {n.props.get('price_band', '')} "
            f"{n.props.get('positioning', '')}" for n in nodes
        ]
        try:
            embed_texts(texts)
        except Exception:  # noqa: BLE001
            return 0
        return len(nodes)

    def incremental_update(self, extraction: ExtractionResult, since: datetime) -> Dict[str, Any]:
        """增量更新：只处理指定时间之后的新证据。"""
        fresh_entities = []
        fresh_triples = []
        for entity in extraction.entities:
            if any((ev.published_at or datetime.min) >= since for ev in entity.evidence) or not entity.evidence:
                fresh_entities.append(entity)
        for triple in extraction.triples:
            if any((ev.published_at or datetime.min) >= since for ev in triple.evidence) or not triple.evidence:
                fresh_triples.append(triple)
        return self.build(ExtractionResult(entities=fresh_entities, triples=fresh_triples,
                                           notes="incremental"))
