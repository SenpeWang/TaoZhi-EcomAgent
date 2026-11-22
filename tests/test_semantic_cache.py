"""语义缓存的越权与脱敏边界。

缓存按问题相似度命中，命中者未必是当初提问的那个人，所以"密级"之外还
要单独判"脱敏"。这批用例锁的就是这条边界——它不是可选优化项。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

from taozhi.knowledge import semantic_cache as sc
from taozhi.knowledge.semantic_cache import SemanticCache

PHONE = "13812345678"
RAW_ANSWER = f"客户联系电话 {PHONE}，可以正常退货。"
RAW_QUOTE = f"收货地址：上海市浦东新区某某路 1 号，联系人手机 {PHONE}。"


@pytest.fixture
def cache(monkeypatch):
    """同一向量 = 任何提问都相似命中；把脱敏差异单独拎出来测。"""
    monkeypatch.setattr(sc, "embedding_available", lambda: True)
    monkeypatch.setattr(sc, "encode_query", lambda q: np.ones((1, 512), dtype="float32"))
    return SemanticCache(similarity_threshold=0.5)


def test_staff_hitting_boss_entry_gets_redacted(cache):
    """boss 的未脱敏原文，被 staff 的相似问题命中后必须已脱敏。"""
    cache.put("t1", "退货怎么走", RAW_ANSWER,
              [{"title": "退货政策", "quote": RAW_QUOTE}], [{"kind": "document", "document_id": "d1"}],
              input_level=1, redacted=False)

    hit = cache.get("t1", "退货流程是什么", input_level=1, boss=False)

    assert hit is not None
    assert PHONE not in hit["answer"]
    assert PHONE not in hit["citations"][0]["quote"]
    assert "上海市浦东新区" not in hit["citations"][0]["quote"]


@pytest.mark.parametrize("sources", [
    [],
    [{"kind": "metric", "metric_id": "m1"}],
    [{"kind": "document", "document_id": "d1"}, {"kind": "business", "identifier": "order1"}],
])
def test_shared_cache_accepts_only_document_evidence(cache, sources):
    """任何实时数据依赖都使整份答案无法成为跨问题的共享候选。"""
    assert not cache.put("t1", "当前状态", "只对当前请求有效", [], sources)
    assert cache.get("t1", "当前状态") is None


def test_embedding_dimension_change_replaces_incompatible_entries(cache, monkeypatch):
    """编码服务切换向量维度后以新样本恢复命中，禁止拼接不同维度的答案。"""
    sources = [{"kind": "document", "document_id": "d1"}]
    assert cache.put("t1", "旧问题", "旧答案", [], sources)
    monkeypatch.setattr(sc, "encode_query", lambda q: np.ones((1, 32), dtype="float32"))
    assert cache.get("t1", "新问题") is None
    assert cache.put("t1", "新问题", "新答案", [], sources)
    assert cache.get("t1", "新问题")["answer"] == "新答案"


def test_boss_gets_own_entry_untouched(cache):
    """boss 命中自己那批内容，不该被多余地脱敏一次。"""
    cache.put("t1", "退货怎么走", RAW_ANSWER,
              [{"title": "退货政策", "quote": RAW_QUOTE}], [{"kind": "document", "document_id": "d1"}],
              input_level=1, redacted=False)

    hit = cache.get("t1", "退货流程是什么", input_level=1, boss=True)

    assert hit["answer"] == RAW_ANSWER
    assert hit["citations"][0]["quote"] == RAW_QUOTE


def test_boss_skips_redacted_entry(cache):
    """boss 命中 staff 的已脱敏条目会拿到残缺内容，宁可算未命中走完整链路。"""
    cache.put("t1", "退货怎么走", "客户联系电话 138****5678。",
              [{"title": "退货政策", "quote": "已脱敏"}], [{"kind": "document", "document_id": "d1"}],
              input_level=1, redacted=True)

    assert cache.get("t1", "退货流程是什么", input_level=1, boss=True) is None


def test_entry_without_quote_is_left_alone(cache):
    """没有 quote 的引用不能凭空长出字段。"""
    cache.put("t1", "退货怎么走", RAW_ANSWER, [{"title": "退货政策"}],
              [{"kind": "document", "document_id": "d1"}], input_level=1, redacted=False)

    hit = cache.get("t1", "退货流程是什么", input_level=1, boss=False)

    assert hit["citations"][0] == {"title": "退货政策"}


def test_level_gate_still_blocks_upward(cache):
    """脱敏对齐不能把原来的密级闸门顶掉：L2 条目对 input_level=1 仍不可见。"""
    cache.put("t1", "毛利多少", "毛利 30%", [{"title": "经营", "quote": "毛利"}],
              [{"kind": "document", "document_id": "d2"}], input_level=2, redacted=False)

    assert cache.get("t1", "毛利率", input_level=1, boss=False) is None


def test_boss_skips_redacted_without_occluding_boss_entry(monkeypatch):
    """staff 已脱敏条目的相似度更高时，boss 应跳过它并命中同问题未脱敏的 boss 条目。"""
    monkeypatch.setattr(sc, "embedding_available", lambda: True)
    v_high = np.zeros(512, dtype="float32")
    v_high[0] = 1.0
    v_mid = np.zeros(512, dtype="float32")
    v_mid[0] = 0.9
    v_mid[1] = 0.435
    seq = iter([np.expand_dims(v_high, 0), np.expand_dims(v_mid, 0), np.expand_dims(v_high, 0)])
    monkeypatch.setattr(sc, "encode_query", lambda q: next(seq))

    cache_inst = SemanticCache(similarity_threshold=0.8)
    cache_inst.put("t1", "客户电话多少", "138****5678", [{"title": "资料", "quote": "138****5678"}],
                   [{"kind": "document", "document_id": "d1"}], input_level=1, redacted=True)
    cache_inst.put("t1", "客户电话是什么", RAW_ANSWER, [{"title": "资料", "quote": RAW_QUOTE}],
                   [{"kind": "document", "document_id": "d1"}], input_level=1, redacted=False)

    hit = cache_inst.get("t1", "客户电话多少", input_level=1, boss=True)
    assert hit is not None
    assert hit["answer"] == RAW_ANSWER


def test_invalidate_by_document_and_entry_id(cache):
    """按文档 ID 或条目 ID 精准驱逐后，其余文档缓存仍保留。"""
    cache.put("t1", "问题A", "答案A", [{"title": "D1", "quote": "q1"}], [{"kind": "document", "document_id": "d1"}])
    cache.put("t1", "问题B", "答案B", [{"title": "D2", "quote": "q2"}], [{"kind": "document", "document_id": "d2"}])

    removed = cache.invalidate("t1", document_id="d1")
    assert removed == 1
    hit = cache.get("t1", "问题B", input_level=1, boss=False)
    assert hit is not None and hit["answer"] == "答案B"
    assert cache.invalidate("t1", entry_id=hit["entry_id"]) == 1
    assert cache.get("t1", "问题B", input_level=1, boss=False) is None


def test_cross_entity_query_does_not_collide(cache):
    """不同商品型号/实体（如 LX-20 vs LX-21）即使向量极高相似也禁止互相命中缓存。"""
    cache.put(
        "t1",
        "用 LX-20 65W 氮化镓充电器搭配 iPhone 15 兼容性结论是什么？",
        "LX-20 适配 iPhone 15，峰值 65W",
        [{"title": "LX-20 规格", "quote": "65W"}],
        [{"kind": "document", "document_id": "d_lx20"}],
    )
    assert (
        cache.get(
            "t1",
            "用 LX-21 140W 氮化镓充电器搭配 MacBook Pro 16 兼容性结论是什么？",
            input_level=1,
            boss=False,
        )
        is None
    )
    unspecified_wattage = cache.get(
        "t1",
        "用 LX-20 充电器搭配 iPhone 15 行不行？",
        input_level=1,
        boss=False,
    )
    assert unspecified_wattage is None
    same = cache.get("t1", "用 LX-20 65W 氮化镓充电器搭配 iPhone 15 兼容性结论是什么?", input_level=1, boss=False)
    assert same is not None and "65W" in same["answer"]


def test_policy_cache_preserves_channel_and_time_conditions(cache):
    source = [{"kind": "document", "document_id": "policy"}]
    cache.put("t1", "本店线上渠道的保修期？", "以所引线上条款为准", [], source, redacted=True)
    assert cache.get("t1", "本店线下渠道的保修期？") is None
    assert cache.get("t1", "本店线上渠道的保修期是否变更？") is None
    assert cache.get("t1", "本店线上渠道的保修期?")["answer"] == "以所引线上条款为准"
