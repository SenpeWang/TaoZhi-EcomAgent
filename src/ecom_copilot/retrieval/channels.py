"""四路召回通道：向量 / BM25 / HyDE / 图谱（PRD 模块 D）。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

import numpy as np

from ..config import Settings, get_settings
from ..llm import ModelTier, get_llm
from ..observability import record_retrieval
from ..schemas.common import Evidence, PermissionContext, TextChunk
from ..schemas.graph import GraphQueryResult
from .index import KnowledgeIndex


class RetrievalHit:
    """统一召回结果。"""

    __slots__ = ("chunk", "score", "channel", "rank")

    def __init__(self, chunk: TextChunk, score: float, channel: str, rank: int) -> None:
        self.chunk = chunk
        self.score = float(score)
        self.channel = channel
        self.rank = rank

    def to_evidence(self) -> Evidence:
        return self.chunk.to_evidence(
            quote=self.chunk.text[:220],
            score=self.score,
            confidence=min(0.95, 0.4 + self.score),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Hit {self.channel}#{self.rank} {self.score:.3f} {self.chunk.doc_name}>"


class BaseChannel:
    name = "base"

    def __init__(self, index: KnowledgeIndex, settings: Optional[Settings] = None) -> None:
        self.index = index
        self.settings = settings or get_settings()

    def retrieve(self, query: str, top_k: int,
                 permission: Optional[PermissionContext] = None) -> List[RetrievalHit]:
        raise NotImplementedError

    @staticmethod
    def _visible(chunk: TextChunk, permission: Optional[PermissionContext]) -> bool:
        if permission is None:
            return True
        from ..config import get_settings
        if not chunk.doc_id and get_settings().app_env.lower() in ("prod","production"):
            return False
        return permission.visible(chunk.owner_id, chunk.org_tag, chunk.is_public,chunk.doc_id)

    def _top(self, scores: np.ndarray, top_k: int, channel: str,
             permission: Optional[PermissionContext]) -> List[RetrievalHit]:
        if not len(self.index.chunks):
            return []
        order = np.argsort(-scores)  # Apply ACL before limiting tenant candidates.
        hits: List[RetrievalHit] = []
        rank = 0
        for idx in order:
            score = float(scores[idx])
            if score <= 1e-6:
                continue
            chunk = self.index.chunks[int(idx)]
            if not self._visible(chunk, permission):
                continue
            rank += 1
            hits.append(RetrievalHit(chunk, score, channel, rank))
            if len(hits) >= top_k:
                break
        record_retrieval(channel, len(hits), 1)
        return hits


class DenseChannel(BaseChannel):
    """D1 向量召回：Milvus 2.6（dense+sparse 混合）→ 本地向量索引。"""

    name = "dense"

    def __init__(self, index: KnowledgeIndex, settings: Optional[Settings] = None) -> None:
        super().__init__(index, settings)
        self.milvus = None
        if self.settings.milvus_enabled:
            self.milvus = self._connect()

    def _connect(self):
        try:
            from pymilvus import MilvusClient  # noqa: PLC0415

            client = MilvusClient(uri=self.settings.milvus_uri)
            return client
        except Exception:  # noqa: BLE001
            return None

    def retrieve(self, query: str, top_k: int,
                 permission: Optional[PermissionContext] = None) -> List[RetrievalHit]:
        if self.milvus is not None:
            hits = self._milvus_search(query, top_k, permission)
            if hits:
                return hits
        return self._top(self.index.dense_scores(query), top_k, self.name, permission)

    def _milvus_search(self, query: str, top_k: int,
                       permission: Optional[PermissionContext]) -> List[RetrievalHit]:
        from ..llm import embed_one  # noqa: PLC0415

        try:
            vector = embed_one(query).tolist()
            rows = self.milvus.search(
                collection_name=self.settings.milvus_collection,
                data=[vector], limit=top_k * 2,
                output_fields=["chunk_id", "text", "doc_name", "page", "org_tag", "is_public"],
            )
        except Exception:  # noqa: BLE001
            return []
        hits = []
        for rank, item in enumerate(rows[0] if rows else [], start=1):
            entity = item.get("entity", {})
            chunk = TextChunk(
                id=str(entity.get("chunk_id", "")),
                doc_name=str(entity.get("doc_name", "")),
                page=entity.get("page"),
                text=str(entity.get("text", "")),
                org_tag=str(entity.get("org_tag", "")),
                is_public=bool(entity.get("is_public", True)),
            )
            if not self._visible(chunk, permission):
                continue
            hits.append(RetrievalHit(chunk, float(item.get("distance", 0.0)), self.name, rank))
        return hits[:top_k]


class BM25Channel(BaseChannel):
    """D2 BM25 关键词召回：ES 9.x + IK 分词 → rank_bm25。"""

    name = "bm25"

    def __init__(self, index: KnowledgeIndex, settings: Optional[Settings] = None) -> None:
        super().__init__(index, settings)
        self.es = None
        if self.settings.es_enabled:
            self.es = self._connect()

    def _connect(self):
        try:
            from elasticsearch import Elasticsearch  # noqa: PLC0415

            client = Elasticsearch(self.settings.es_url, verify_certs=False)
            if client.ping():
                self._ensure_index(client)
                return client
        except Exception:  # noqa: BLE001
            return None
        return None

    def _ensure_index(self, client) -> None:
        mapping = {
            "settings": {"analysis": {"analyzer": {
                "ik_analyzer": {"type": "custom", "tokenizer": "ik_max_word"}}}},
            "mappings": {"properties": {
                "text": {"type": "text", "analyzer": "ik_analyzer"},
                "doc_name": {"type": "text", "analyzer": "ik_analyzer"},
                "org_tag": {"type": "keyword"},
                "is_public": {"type": "boolean"},
            }},
        }
        try:
            if not client.indices.exists(index=self.settings.es_index):
                client.indices.create(index=self.settings.es_index, body=mapping)
        except Exception:  # noqa: BLE001
            pass

    def retrieve(self, query: str, top_k: int,
                 permission: Optional[PermissionContext] = None) -> List[RetrievalHit]:
        if self.es is not None:
            hits = self._es_search(query, top_k, permission)
            if hits:
                return hits
        return self._top(self.index.bm25_scores(query), top_k, self.name, permission)

    def _es_search(self, query: str, top_k: int,
                   permission: Optional[PermissionContext]) -> List[RetrievalHit]:
        try:
            body = {"size": top_k * 2, "query": {"match": {"text": query}}}
            if permission is not None and not permission.is_admin:
                body["query"] = {"bool": {"must": [{"match": {"text": query}}],
                                          "should": [{"term": {"is_public": True}},
                                                     {"term": {"org_tag": permission.org_tag}}],
                                          "minimum_should_match": 1}}
            resp = self.es.search(index=self.settings.es_index, body=body)
        except Exception:  # noqa: BLE001
            return []
        hits = []
        for rank, item in enumerate(resp.get("hits", {}).get("hits", []), start=1):
            src = item.get("_source", {})
            chunk = TextChunk(
                id=item.get("_id", ""),
                doc_name=str(src.get("doc_name", "")),
                page=src.get("page"),
                text=str(src.get("text", "")),
                org_tag=str(src.get("org_tag", "")),
                is_public=bool(src.get("is_public", True)),
            )
            if not self._visible(chunk,permission):continue
            hits.append(RetrievalHit(chunk, float(item.get("_score", 0.0)), self.name, rank))
        return hits[:top_k]


class HyDEChannel(BaseChannel):
    """D3 HyDE 召回：LLM 生成假设答案 → 用假设答案做向量检索，提升长尾召回。"""

    name = "hyde"

    PROMPT = """你是手机配件私域客服专家。针对下面的问题，写一段 3-5 句的"假设性回答"，
只写内容本身，用于语义检索（可包含机型、膜壳型号、设备型号、参数名）。

问题：{query}
假设性回答："""

    def retrieve(self, query: str, top_k: int,
                 permission: Optional[PermissionContext] = None) -> List[RetrievalHit]:
        if not self.settings.hyde_enabled or not self.settings.has_llm:
            return []
        try:
            hypothesis = get_llm().chat(
                self.PROMPT.format(query=query), tier=ModelTier.FAST, max_tokens=600
            )
        except Exception:  # noqa: BLE001
            return []
        if not hypothesis:
            return []
        scores = self.index.dense_scores(hypothesis)
        blended = 0.7 * scores + 0.3 * self.index.dense_scores(query)
        return self._top(blended, top_k, self.name, permission)


class GraphChannel(BaseChannel):
    """D4 图谱召回：Neo4j Cypher 查询结构化关系。"""

    name = "graph"

    def __init__(self, index: KnowledgeIndex, settings: Optional[Settings] = None,
                 graph_store=None) -> None:
        super().__init__(index, settings)
        self.graph_store = graph_store

    def retrieve(self, query: str, top_k: int,
                 permission: Optional[PermissionContext] = None) -> List[RetrievalHit]:
        if self.graph_store is None:
            return []
        entities = _extract_entity_candidates(query)
        hits: List[RetrievalHit] = []
        rank = 0
        for name in entities[:5]:
            try:
                relations = self.graph_store.neighbors(name, depth=1)
            except Exception:  # noqa: BLE001
                continue
            for rel in relations[:6]:
                rank += 1
                text = _relation_text(rel)
                chunk = TextChunk(
                    id=f"graph:{rel.get('source')}-{rel.get('relation')}-{rel.get('target')}",
                    doc_name="商品适配图谱",
                    section=rel.get("relation", ""),
                    text=text,
                    meta={"graph": True},
                )
                hits.append(RetrievalHit(chunk, 0.6 + 0.05 * (6 - min(rank, 6)), self.name, rank))
        record_retrieval(self.name, len(hits), 1)
        return hits[:top_k]

    def cypher(self, query: str) -> GraphQueryResult:
        from ..graph.cypher_lite import run_cypher

        return run_cypher(query, self.graph_store, include_evidence=True)


# 内置商品型号词表（品牌膜法工坊 MofaLab：膜壳 / 膜切机 / UV 打印机 / 配件与机型词）
_MODEL_VOCAB = (
    "C1", "C2", "C3", "C5", "L1",                # 钢化膜 / 镜头膜
    "S10", "T20", "A30",                         # 手机壳
    "P1", "K1", "G1",                            # 贴膜神器 / 清洁套装 / 指环支架
    "MC-300", "MC-500",                          # 膜切机
    "UV-600", "UV-900",                          # UV 打印机
    "iPhone", "Mate", "小米", "折叠屏",           # 适配机型词
)

# 商品域实体模式：品类 / 配件 / 常见故障（第一支允许携带「膜法工坊」品牌前缀）
_ECOM_ENTITY_RE = re.compile(
    r"膜法工坊[\u4e00-\u9fa5]{0,4}?(?:钢化膜|手机壳|手机膜|防窥膜|陶瓷膜|镜头膜|贴膜神器|膜切机|UV打印机|起泡|堵头|切歪|连不上蓝牙|碎边|发黄|脱胶)"
    r"|钢化膜|手机壳|手机膜|防窥膜|陶瓷膜|镜头膜|贴膜神器|膜切机|切膜机|UV打印机|配件|耗材|起泡|堵头|切歪|连不上蓝牙|碎边|发黄|脱胶"
)


def _extract_entity_candidates(query: str) -> List[str]:
    """从问题中抽取可能的实体名（商品型号 / 机型 / 品类）。"""
    # 1) 内置型号词表按词边界精确命中（如 MC-500 / C3 / iPhone）
    candidates = [m for m in _MODEL_VOCAB
                  if re.search(rf"(?<![A-Za-z0-9]){re.escape(m)}(?![A-Za-z0-9])", query)]
    # 2) 配件 / 品类 / 故障等商品域实体
    candidates += _ECOM_ENTITY_RE.findall(query)
    return [c for c in dict.fromkeys(candidates) if c]


def _relation_text(rel: dict) -> str:
    mapping = {
        "BELONGS_TO_BRAND": "属于品牌", "IN_CATEGORY": "属于类目",
        "HAS_SKU": "包含SKU", "COMPATIBLE_WITH": "适配",
        "SUPERSEDES": "升级替代", "RELATES_TO_ISSUE": "关联故障",
    }
    verb = mapping.get(str(rel.get("relation", "")), "关联")
    return f"{rel.get('source')} {verb} {rel.get('target')}（置信度 {rel.get('confidence', 0.5):.2f}）"


def build_channels(index: KnowledgeIndex, settings: Optional[Settings] = None,
                   graph_store=None) -> List[BaseChannel]:
    cfg = settings or get_settings()
    return [
        DenseChannel(index, cfg),
        BM25Channel(index, cfg),
        HyDEChannel(index, cfg),
        GraphChannel(index, cfg, graph_store),
    ]
