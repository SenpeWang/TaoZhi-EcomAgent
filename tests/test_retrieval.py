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


# ── 企业在线链路 Rerank 精排（enterprise/retriever._rerank_order） ──

import numpy as np

from ecom_copilot.enterprise import embedding as ent_embedding
from ecom_copilot.enterprise import retriever as ent_retriever


class _Cfg:
    def __init__(self, enabled=True, candidates=20):
        self.rerank_enabled = enabled
        self.rerank_candidates = candidates


def _fused_chunks(n=5):
    chunks = [{"id": f"k{i}", "text": f"切片{i} 膜切机参数"} for i in range(n)]
    fused = np.linspace(0.01, 0.03, n)  # 融合分按索引递增：i 越大融合分越高
    return chunks, fused


def test_enterprise_rerank_reorders_candidates(monkeypatch):
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    # 客户端打分恰好把融合序反转，验证精排生效
    monkeypatch.setattr(ent_embedding, "rerank",
                        lambda q, texts: [float(len(texts) - j) for j in range(len(texts))])
    order = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert order[0] == 4 and order[-1] == 0
    assert sorted(order) == list(range(5))


def test_enterprise_rerank_failure_falls_back_to_fused(monkeypatch):
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())

    def _boom(q, texts):
        raise RuntimeError("服务不可用")

    monkeypatch.setattr(ent_embedding, "rerank", _boom)
    order = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert order == [4, 3, 2, 1, 0]  # 融合分降序


def test_enterprise_rerank_disabled_keeps_fused_order(monkeypatch):
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(enabled=False))

    def _unexpected(q, texts):
        raise AssertionError("关闭精排时不应调用客户端")

    monkeypatch.setattr(ent_embedding, "rerank", _unexpected)
    assert ent_retriever._rerank_order("膜切机", chunks, fused) == [4, 3, 2, 1, 0]


def test_enterprise_rerank_floor_and_candidates(monkeypatch):
    chunks, fused = _fused_chunks()
    # 候选数上限 2：只有融合分最高的 2 条进入精排
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(candidates=2))
    seen = {}

    def _fake(q, texts):
        seen["texts"] = texts
        return [0.9, 0.1]

    monkeypatch.setattr(ent_embedding, "rerank", _fake)
    order = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert order == [4, 3]
    assert len(seen["texts"]) == 2
    # 融合分低于 RRF_FLOOR 的候选全部剔除
    low = np.array([0.0001] * 5)
    assert ent_retriever._rerank_order("膜切机", chunks, low) == []
