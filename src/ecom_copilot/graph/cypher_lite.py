"""轻量 Cypher 子集解析器。

支持形如：
    MATCH (p:Product)-[:HAS_SKU]->(s:Sku) RETURN p LIMIT 20
    MATCH (a:Accessory)-[:COMPATIBLE_WITH]->(p:Product) WHERE p.name='防窥膜C3' RETURN a
    MATCH (n:Product) WHERE n.price_band='旗舰' RETURN n

用于在没有 Neo4j 时让 /api/graph/query 依然可用（NetworkX 后端直接执行）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..schemas.common import Evidence
from ..schemas.graph import EdgeType, GraphQueryResult, NodeType
from .store import GraphStore

_PATH_RE = re.compile(
    r"\(\s*(?P<v1>\w+)\s*(?::\s*(?P<l1>\w+))?\s*\)\s*"
    r"-\s*\[\s*(?::\s*(?P<rel>\w+))?\s*\]\s*->\s*"
    r"\(\s*(?P<v2>\w+)\s*(?::\s*(?P<l2>\w+))?\s*\)"
)
_SINGLE_RE = re.compile(r"\(\s*(?P<v1>\w+)\s*(?::\s*(?P<l1>\w+))?\s*\)")
_WHERE_RE = re.compile(r"(?P<var>\w+)\.(?P<prop>\w+)\s*(?:=|CONTAINS)\s*'?(?P<val>[^'\s]+)'?")
_RETURN_RE = re.compile(r"RETURN\s+(?P<vars>[\w,\s]+?)(?:\s+LIMIT|$)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"LIMIT\s+(\d+)", re.IGNORECASE)


def run_cypher(query: str, store: GraphStore,
               include_evidence: bool = False) -> GraphQueryResult:
    import time

    started = time.perf_counter()
    result = GraphQueryResult(cypher=query, backend=getattr(store, "backend", "networkx"))

    limit_match = _LIMIT_RE.search(query)
    limit = int(limit_match.group(1)) if limit_match else 50

    where = {m.group("var") + "." + m.group("prop"): m.group("val")
             for m in _WHERE_RE.finditer(query)}
    return_match = _RETURN_RE.search(query)
    return_vars = [v.strip() for v in return_match.group("vars").split(",")] if return_match else ["n"]

    path = _PATH_RE.search(query)
    rows: List[Dict[str, Any]] = []
    evidence: List[Evidence] = []

    if path:
        rel = path.group("rel")
        edge_types = [EdgeType(rel)] if rel and _is_edge_type(rel) else None
        for edge in store.edges(edge_types[0]) if edge_types else store.edges():
            bindings = {path.group("v1"): edge.source, path.group("v2"): edge.target}
            if not _match_where(bindings, where, store,
                                {path.group("v1"): edge.source, path.group("v2"): edge.target}):
                continue
            row: Dict[str, Any] = {}
            for var in return_vars:
                name = bindings.get(var)
                node = store.get_node(name) if name else None
                row[var] = {
                    "name": name,
                    "type": node.type.value if node else "Product",
                    "props": dict(node.props) if node else {},
                } if node else {"name": name}
            row["relation"] = edge.type.value
            row["confidence"] = edge.confidence
            rows.append(row)
            if include_evidence:
                evidence.extend(edge.evidence)
            if len(rows) >= limit:
                break
        result.columns = return_vars + ["relation", "confidence"]
    else:
        single = _SINGLE_RE.search(query)
        var = single.group("v1") if single else "n"
        label = single.group("l1") if single else None
        node_type = NodeType(label) if label and _is_node_type(label) else None
        for node in store.nodes(node_type):
            if not _match_where({var: node.name}, where, store, {var: node.name}):
                continue
            rows.append({var: {"name": node.name, "type": node.type.value,
                               "props": dict(node.props)}})
            if len(rows) >= limit:
                break
        result.columns = return_vars

    result.rows = rows
    result.evidence = evidence[:20]
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


def _is_edge_type(value: str) -> bool:
    try:
        EdgeType(value)
        return True
    except ValueError:
        return False


def _is_node_type(value: str) -> bool:
    try:
        NodeType(value)
        return True
    except ValueError:
        return False


def _match_where(bindings: Dict[str, str], where: Dict[str, str],
                 store: GraphStore, names: Dict[str, str]) -> bool:
    for key, expected in where.items():
        var, prop = key.split(".", 1)
        name = names.get(var)
        if name is None:
            continue
        node = store.get_node(name)
        if node is None:
            return False
        if prop in ("name",):
            actual = node.name
        elif prop == "id":
            actual = node.id
        else:
            actual = str(node.props.get(prop, ""))
        if expected not in str(actual):
            return False
    return True
