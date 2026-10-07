"""商品知识图谱存储：Neo4j 5.x + NetworkX 本地降级。

节点 Brand/Category/Product/Sku/Accessory/Issue；
边 BELONGS_TO_BRAND/IN_CATEGORY/HAS_SKU/COMPATIBLE_WITH/SUPERSEDES/RELATES_TO_ISSUE。
每条边强制携带 evidence + confidence；更新时旧边保留历史版本（valid_to）。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import networkx as nx

from ..config import Settings, get_settings
from ..schemas.common import PermissionContext
from ..schemas.graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    GraphQueryResult,
    GraphStats,
    NodeType,
    PriceBand,
)


class GraphStore:
    """图谱存储统一接口。"""

    backend: str = "base"

    # ── 写 ──
    def upsert_node(self, node: GraphNode) -> GraphNode:
        raise NotImplementedError

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        raise NotImplementedError

    # ── 读 ──
    def get_node(self, name: str) -> Optional[GraphNode]:
        raise NotImplementedError

    def neighbors(self, name: str, edge_types: Optional[List[EdgeType]] = None,
                  direction: str = "both", depth: int = 1) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def nodes(self, node_type: Optional[NodeType] = None,
              price_band: Optional[PriceBand] = None) -> List[GraphNode]:
        raise NotImplementedError

    def edges(self, edge_type: Optional[EdgeType] = None,
              include_history: bool = False) -> List[GraphEdge]:
        raise NotImplementedError

    def stats(self) -> GraphStats:
        raise NotImplementedError

    def search(self, keyword: str, limit: int = 20) -> List[GraphNode]:
        raise NotImplementedError


class NetworkXGraphStore(GraphStore):
    """本地图存储：NetworkX + JSON 持久化（无 Neo4j 时的生产级降级）。"""

    backend = "networkx"

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self.graph = nx.MultiDiGraph()
        self.edge_index: Dict[str, GraphEdge] = {}
        self.edge_history: List[GraphEdge] = []
        self._lock = threading.RLock()
        self.persist_path = Path(persist_path) if persist_path else None
        if self.persist_path and self.persist_path.exists():
            self.load()

    # ───────── 持久化 ─────────
    def save(self) -> None:
        if not self.persist_path:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [n.model_dump(mode="json") for n in self.nodes()],
            "edges": [e.model_dump(mode="json") for e in self.edges()],
            "history": [e.model_dump(mode="json") for e in self.edge_history[-500:]],
        }
        self.persist_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def load(self) -> None:
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            return
        for item in payload.get("nodes", []):
            node = GraphNode.model_validate(item)
            self.graph.add_node(node.name, data=node)
        for item in payload.get("edges", []):
            edge = GraphEdge.model_validate(item)
            self.edge_index[edge.key()] = edge
            self.graph.add_edge(edge.source, edge.target, key=edge.type.value, data=edge)
        for item in payload.get("history", []):
            self.edge_history.append(GraphEdge.model_validate(item))

    # ───────── 写 ─────────
    def upsert_node(self, node: GraphNode) -> GraphNode:
        with self._lock:
            existing = self.get_node(node.name)
            if existing is not None:
                merged = {**existing.props, **{k: v for k, v in node.props.items() if v}}
                existing.props = merged
                existing.updated_at = datetime.utcnow()
                if node.type != NodeType.PRODUCT:
                    existing.type = node.type
                self.graph.nodes[node.name]["data"] = existing
                return existing
            self.graph.add_node(node.name, data=node)
            return node

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        with self._lock:
            self.upsert_node(GraphNode(name=edge.source, type=NodeType.PRODUCT))
            self.upsert_node(GraphNode(name=edge.target, type=NodeType.PRODUCT))
            key = edge.key()
            existing = self.edge_index.get(key)
            if existing is not None:
                changed = (
                    abs(existing.confidence - edge.confidence) > 0.02
                    or (existing.props or {}) != (edge.props or {})
                )
                if not changed:
                    # 仅补充证据，不产生新版本
                    known = {e.quote for e in existing.evidence}
                    for ev in edge.evidence:
                        if ev.quote not in known:
                            existing.evidence.append(ev)
                    return existing
                # 旧版本归档
                existing.valid_to = datetime.utcnow()
                self.edge_history.append(existing)
                edge.version = existing.version + 1
                edge.valid_from = datetime.utcnow()
            self.edge_index[key] = edge
            if self.graph.has_edge(edge.source, edge.target, key=edge.type.value):
                self.graph.remove_edge(edge.source, edge.target, key=edge.type.value)
            self.graph.add_edge(edge.source, edge.target, key=edge.type.value, data=edge)
            return edge

    # ───────── 读 ─────────
    def get_node(self, name: str) -> Optional[GraphNode]:
        if name in self.graph.nodes:
            return self.graph.nodes[name].get("data")
        return None

    def nodes(self, node_type: Optional[NodeType] = None,
              price_band: Optional[PriceBand] = None) -> List[GraphNode]:
        result: List[GraphNode] = []
        for _, data in self.graph.nodes(data=True):
            node: GraphNode = data.get("data")
            if node is None:
                continue
            if node_type and node.type != node_type:
                continue
            if price_band and node.props.get("price_band") != price_band.value:
                continue
            result.append(node)
        return result

    def edges(self, edge_type: Optional[EdgeType] = None,
              include_history: bool = False) -> List[GraphEdge]:
        items = list(self.edge_index.values())
        if edge_type:
            items = [e for e in items if e.type == edge_type]
        if include_history:
            items = items + self.edge_history
        return items

    def neighbors(self, name: str, edge_types: Optional[List[EdgeType]] = None,
                  direction: str = "both", depth: int = 1) -> List[Dict[str, Any]]:
        if name not in self.graph:
            return []
        allowed = {t.value for t in edge_types} if edge_types else None
        out: List[Dict[str, Any]] = []
        seen = set()

        def walk(node: str, level: int) -> None:
            if level > depth:
                return
            for src, dst, key, data in list(self.graph.out_edges(node, keys=True, data=True)):
                if allowed and key not in allowed:
                    continue
                if (src, dst, key) in seen:
                    continue
                seen.add((src, dst, key))
                out.append({"source": src, "target": dst, "relation": key,
                            "depth": level, **_edge_brief(data.get("data"))})
                if direction in ("both", "out"):
                    walk(dst, level + 1)
            if direction in ("both", "in"):
                for src, dst, key, data in list(self.graph.in_edges(node, keys=True, data=True)):
                    if allowed and key not in allowed:
                        continue
                    if (src, dst, key) in seen:
                        continue
                    seen.add((src, dst, key))
                    out.append({"source": src, "target": dst, "relation": key,
                                "depth": level, **_edge_brief(data.get("data"))})
                    walk(src, level + 1)

        walk(name, 1)
        return out

    def search(self, keyword: str, limit: int = 20) -> List[GraphNode]:
        keyword = (keyword or "").strip()
        if not keyword:
            return self.nodes()[:limit]
        hits = [n for n in self.nodes() if keyword in n.name]
        return hits[:limit]

    def stats(self) -> GraphStats:
        stats = GraphStats(node_count=self.graph.number_of_nodes(),
                           edge_count=len(self.edge_index))
        for node in self.nodes():
            stats.by_node_type[node.type.value] = stats.by_node_type.get(node.type.value, 0) + 1
            band = node.props.get("price_band")
            if band:
                stats.by_price_band[band] = stats.by_price_band.get(band, 0) + 1
        for edge in self.edges():
            stats.by_edge_type[edge.type.value] = stats.by_edge_type.get(edge.type.value, 0) + 1
        return stats

    def to_vis_payload(self, limit: int = 300) -> Dict[str, Any]:
        """供前端 AntV G6 / ECharts 渲染。"""
        nodes = []
        for node in self.nodes()[:limit]:
            nodes.append({
                "id": node.name,
                "label": node.name,
                "type": node.type.value,
                "price_band": node.props.get("price_band", PriceBand.UNKNOWN.value),
                "props": node.props,
            })
        edges = []
        for edge in self.edges()[:limit]:
            edges.append({
                "source": edge.source,
                "target": edge.target,
                "relation": edge.type.value,
                "confidence": edge.confidence,
                "evidence": [e.citation_text for e in edge.evidence][:3],
                "version": edge.version,
            })
        return {"nodes": nodes, "edges": edges}


def _edge_brief(edge: Optional[GraphEdge]) -> Dict[str, Any]:
    if edge is None:
        return {"confidence": 0.0, "evidence": [], "props": {}}
    return {
        "confidence": edge.confidence,
        "evidence": [e.citation_text for e in edge.evidence][:3],
        "props": edge.props,
        "version": edge.version,
    }


class Neo4jGraphStore(GraphStore):
    """Neo4j 5.x 实现（配置 neo4j_enabled=true 时启用）。"""

    backend = "neo4j"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from neo4j import GraphDatabase  # noqa: PLC0415

        cfg = settings or get_settings()
        self.driver = GraphDatabase.driver(
            cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password)
        )
        self._init_constraints()

    def _init_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT brand_name IF NOT EXISTS FOR (b:Brand) REQUIRE b.name IS UNIQUE",
            "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT product_name IF NOT EXISTS FOR (p:Product) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT sku_id IF NOT EXISTS FOR (s:Sku) REQUIRE s.sku_id IS UNIQUE",
            "CREATE CONSTRAINT accessory_name IF NOT EXISTS FOR (a:Accessory) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT issue_name IF NOT EXISTS FOR (i:Issue) REQUIRE i.name IS UNIQUE",
            "CREATE INDEX product_price_band IF NOT EXISTS FOR (p:Product) ON (p.price_band)",
        ]
        with self.driver.session() as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                except Exception:  # noqa: BLE001
                    continue

    def upsert_node(self, node: GraphNode) -> GraphNode:
        label = node.type.value
        props = {"name": node.name, "org_tag": node.org_tag, "is_public": node.is_public,
                 "updated_at": datetime.utcnow().isoformat(), **node.props}
        query = (
            f"MERGE (n:{label} {{name: $name}}) "
            "SET n += $props RETURN n"
        )
        with self.driver.session() as session:
            session.run(query, name=node.name, props=props)
        return node

    def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        self.upsert_node(GraphNode(name=edge.source, type=NodeType.PRODUCT))
        self.upsert_node(GraphNode(name=edge.target, type=NodeType.PRODUCT))
        query = (
            "MATCH (a {name: $src}), (b {name: $dst}) "
            f"MERGE (a)-[r:{edge.type.value}]->(b) "
            "SET r.confidence = $confidence, r.evidence = $evidence, "
            "r.version = COALESCE(r.version, 0) + 1, r.updated_at = $updated_at, r += $props "
            "RETURN r"
        )
        with self.driver.session() as session:
            session.run(
                query,
                src=edge.source,
                dst=edge.target,
                confidence=edge.confidence,
                evidence=json.dumps([e.model_dump(mode="json") for e in edge.evidence],
                                    ensure_ascii=False, default=str),
                updated_at=datetime.utcnow().isoformat(),
                props=edge.props,
            )
        return edge

    def get_node(self, name: str) -> Optional[GraphNode]:
        query = "MATCH (n {name: $name}) RETURN n, labels(n) AS labels LIMIT 1"
        with self.driver.session() as session:
            record = session.run(query, name=name).single()
            if not record:
                return None
            data = dict(record["n"])
            labels = [l for l in record["labels"] if l != "Product"]
            return GraphNode(name=data.get("name", name),
                             type=NodeType(labels[0]) if labels else NodeType.PRODUCT,
                             props={k: v for k, v in data.items() if k != "name"})

    def nodes(self, node_type=None, price_band=None) -> List[GraphNode]:
        label = node_type.value if node_type else ""
        where = "WHERE true"
        if price_band:
            where += f" AND n.price_band = '{price_band.value}'"
        query = f"MATCH (n{':' + label if label else ''}) {where} RETURN n, labels(n) AS labels LIMIT 500"
        result: List[GraphNode] = []
        with self.driver.session() as session:
            for record in session.run(query):
                data = dict(record["n"])
                labels = [l for l in record["labels"]]
                result.append(GraphNode(name=data.get("name", ""),
                                        type=NodeType(labels[0]) if labels else NodeType.PRODUCT,
                                        props={k: v for k, v in data.items() if k != "name"}))
        return result

    def edges(self, edge_type=None, include_history: bool = False) -> List[GraphEdge]:
        rel = edge_type.value if edge_type else ""
        query = f"MATCH (a)-[r{':' + rel if rel else ''}]->(b) RETURN a.name, b.name, type(r) AS t, r LIMIT 500"
        result: List[GraphEdge] = []
        with self.driver.session() as session:
            for record in session.run(query):
                props = dict(record["r"])
                result.append(
                    GraphEdge(
                        type=EdgeType(record["t"]),
                        source=record["a.name"],
                        target=record["b.name"],
                        confidence=float(props.get("confidence", 0.5) or 0.5),
                        version=int(props.get("version", 1) or 1),
                        props={k: v for k, v in props.items()
                               if k not in {"confidence", "evidence", "version"}},
                    )
                )
        return result

    def neighbors(self, name: str, edge_types=None, direction: str = "both",
                  depth: int = 1) -> List[Dict[str, Any]]:
        rel = "|".join(f":{t.value}" for t in edge_types) if edge_types else ""
        pattern = f"-[r{rel} *1..{max(1, depth)}]->" if direction == "out" else f"-[r{rel} *1..{max(1, depth)}]-"
        query = (
            f"MATCH path = (a {{name: $name}}){pattern}(b) "
            "RETURN a.name AS src, b.name AS dst, [x IN relationships(path) | type(x)] AS rels LIMIT 200"
        )
        out: List[Dict[str, Any]] = []
        with self.driver.session() as session:
            for record in session.run(query, name=name):
                out.append({
                    "source": record["src"], "target": record["dst"],
                    "relation": (record["rels"] or ["COMPATIBLE_WITH"])[-1],
                    "depth": len(record["rels"]),
                })
        return out

    def stats(self) -> GraphStats:
        stats = GraphStats()
        with self.driver.session() as session:
            stats.node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            stats.edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            for record in session.run(
                "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c"
            ):
                stats.by_node_type[record["label"]] = record["c"]
            for record in session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c"
            ):
                stats.by_edge_type[record["t"]] = record["c"]
        return stats

    def search(self, keyword: str, limit: int = 20) -> List[GraphNode]:
        query = "MATCH (n) WHERE n.name CONTAINS $kw RETURN n, labels(n) AS labels LIMIT $limit"
        result: List[GraphNode] = []
        with self.driver.session() as session:
            for record in session.run(query, kw=keyword, limit=limit):
                data = dict(record["n"])
                result.append(GraphNode(name=data.get("name", ""),
                                        type=NodeType(record["labels"][0])
                                        if record["labels"] else NodeType.PRODUCT,
                                        props={k: v for k, v in data.items() if k != "name"}))
        return result

    def close(self) -> None:
        self.driver.close()


_store: Optional[GraphStore] = None
_store_lock = threading.RLock()


def get_graph_store(settings: Optional[Settings] = None) -> GraphStore:
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        cfg = settings or get_settings()
        if cfg.neo4j_enabled:
            try:
                _store = Neo4jGraphStore(cfg)
                return _store
            except Exception:  # noqa: BLE001
                pass
        _store = NetworkXGraphStore(cfg.data_dir / "graph" / "product_graph.json")
        return _store


def set_graph_store(store: GraphStore) -> None:
    global _store
    with _store_lock:
        _store = store


def filter_by_permission(items: Iterable[Any],
                         permission: PermissionContext) -> List[Any]:
    visible = []
    for item in items:
        if permission.visible(getattr(item, "owner_id", ""),
                              getattr(item, "org_tag", ""),
                              getattr(item, "is_public", True)):
            visible.append(item)
    return visible
