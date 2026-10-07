"""检索层测试：索引 / 四路召回 / RRF 融合 / 权限过滤。"""

import pytest

from ecom_copilot.retrieval.fusion import reciprocal_rank_fusion, rerank
from ecom_copilot.retrieval.index import KnowledgeIndex
from ecom_copilot.schemas.common import (
    DocType,
    PermissionContext,
    TextChunk,
)


def _chunks() -> list:
    return [
        TextChunk(id="c1", doc_name="膜切机规格书_MC300_MC500",
                  text="膜法工坊 MC-500 膜切机切幅 120mm，双刀头，支持防窥膜与陶瓷膜切割",
                  doc_type=DocType.SPEC_SHEET),
        TextChunk(id="c2", doc_name="机型适配总表_钢化膜与水凝膜",
                  text="防窥膜 C3 仅适配 iPhone 14~17 直板机型（0.38mm、28°），贴膜神器 P1 辅助装贴",
                  doc_type=DocType.SPEC_SHEET),
        TextChunk(id="c3", doc_name="内部机密", text="膜法工坊内部新品定价规划",
                  org_tag="客服运营组", is_public=False),
    ]


def test_index_add_and_search():
    # 商品事实口径必须进入语料切片
    assert "120mm" in _chunks()[0].text
    assert "C3" in _chunks()[1].text
    assert "0.38mm" in _chunks()[1].text
    index = KnowledgeIndex()
    added = index.add(_chunks())
    assert added == 3
    assert len(index) == 3
    bm25 = index.bm25_scores("MC-500 切幅")
    assert len(bm25) == 3
    assert bm25[0] >= bm25[1]

    dense = index.dense_scores("膜切机 切幅")
    assert dense.shape[0] == 3


def test_rrf_fusion():
    from ecom_copilot.retrieval.channels import RetrievalHit

    chunks = _chunks()
    list_a = [RetrievalHit(chunks[0], 0.9, "dense", 1), RetrievalHit(chunks[1], 0.4, "dense", 2)]
    list_b = [RetrievalHit(chunks[1], 0.8, "bm25", 1), RetrievalHit(chunks[0], 0.3, "bm25", 2)]
    fused = reciprocal_rank_fusion([list_a, list_b], k=60)
    assert fused
    assert fused[0].rank == 1
    # 两条通道都命中的文档应排在前面
    assert "+" in fused[0].channel


def test_permission_filter():
    from ecom_copilot.retrieval.channels import BaseChannel

    anon = PermissionContext(user_id="other", org_tag="售后组")
    owner = PermissionContext(user_id="u1", org_tag="客服运营组")
    chunk = _chunks()[2]
    assert BaseChannel._visible(chunk, anon) is False
    assert BaseChannel._visible(chunk, owner) is False  # Same company does not grant access to private docs.
    chunk.owner_id="u1"
    assert BaseChannel._visible(chunk,owner) is True
    assert BaseChannel._visible(chunk, None) is True


def test_rerank_prefers_overlap():
    from ecom_copilot.retrieval.channels import RetrievalHit

    chunks = _chunks()
    hits = [
        RetrievalHit(chunks[1], 0.9, "dense", 1),
        RetrievalHit(chunks[0], 0.5, "bm25", 2),
    ]
    ranked = rerank("MC-500 120mm 切幅", hits, top_k=2)
    assert ranked[0].chunk.id == "c1"
