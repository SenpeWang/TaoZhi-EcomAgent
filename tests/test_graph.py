"""商品知识图谱：构建 / 增量更新 / 历史版本 / Cypher 子集。"""

from ecom_copilot.graph import run_cypher
from ecom_copilot.graph.builder import IndustryGraphBuilder
from ecom_copilot.graph.store import NetworkXGraphStore
from ecom_copilot.schemas.common import Evidence, Sentiment
from ecom_copilot.schemas.graph import (
    EdgeType,
    EntityMention,
    ExtractionResult,
    NodeType,
    SupplyTriple,
)


def _extraction() -> ExtractionResult:
    ev = Evidence(doc_name="膜切机规格书_MC300_MC500", page=2,
                  quote="MC-500 旗舰膜切机支持切割防窥膜 C3",
                  sentiment=Sentiment.NEUTRAL, confidence=0.8)
    return ExtractionResult(
        entities=[
            EntityMention(name="膜法工坊", type=NodeType.BRAND, evidence=[ev]),
            EntityMention(name="MC-500", type=NodeType.PRODUCT, evidence=[ev]),
            EntityMention(name="C3", type=NodeType.ACCESSORY, evidence=[ev]),
        ],
        triples=[
            SupplyTriple(subject="C3", predicate=EdgeType.COMPATIBLE_WITH, obj="MC-500",
                         evidence=[ev], confidence=0.9),
            SupplyTriple(subject="MC-500", predicate=EdgeType.BELONGS_TO_BRAND, obj="膜法工坊",
                         evidence=[ev], confidence=0.85),
        ],
    )


def test_build_and_stats():
    store = NetworkXGraphStore()
    builder = IndustryGraphBuilder(store=store)
    result = builder.build(_extraction())
    assert result["new_nodes"] == 3
    assert result["new_edges"] == 2
    stats = builder.stats()
    assert stats.node_count == 3 and stats.edge_count == 2


def test_incremental_update_keeps_history():
    store = NetworkXGraphStore()
    builder = IndustryGraphBuilder(store=store)
    builder.build(_extraction())
    # 同一关系置信度变化 → 旧版本归档
    extraction = _extraction()
    extraction.triples[0].confidence = 0.5
    result = builder.build(extraction)
    assert result["updated_edges"] >= 1
    assert len(store.edge_history) >= 1


def test_compatibility_and_brand():
    store = NetworkXGraphStore()
    builder = IndustryGraphBuilder(store=store)
    builder.build(_extraction())
    compat = store.neighbors("MC-500", [EdgeType.COMPATIBLE_WITH], "in", 1)
    assert any(item.get("source") == "C3" for item in compat)
    brand = store.neighbors("MC-500", [EdgeType.BELONGS_TO_BRAND], "out", 1)
    assert any(item.get("target") == "膜法工坊" for item in brand)


def test_cypher_relationship_query():
    store = NetworkXGraphStore()
    IndustryGraphBuilder(store=store).build(_extraction())
    result = run_cypher(
        f"MATCH (a)-[:COMPATIBLE_WITH]->(p:{NodeType.PRODUCT.value}) "
        f"WHERE p.name='MC-500' RETURN a",
        store,
    )
    assert result.rows and result.rows[0]["a"]["name"] == "C3"


def test_cypher_node_query_with_props():
    store = NetworkXGraphStore()
    IndustryGraphBuilder(store=store).build(_extraction())
    result = run_cypher(
        f"MATCH (n:{NodeType.PRODUCT.value}) WHERE n.name='MC-500' RETURN n", store)
    assert [row["n"]["name"] for row in result.rows] == ["MC-500"]


def test_stats_by_type():
    store = NetworkXGraphStore()
    builder = IndustryGraphBuilder(store=store)
    builder.build(_extraction())
    stats = builder.stats()
    assert stats.by_edge_type.get(EdgeType.COMPATIBLE_WITH.value) == 1
    assert stats.by_node_type.get(NodeType.ACCESSORY.value) == 1
