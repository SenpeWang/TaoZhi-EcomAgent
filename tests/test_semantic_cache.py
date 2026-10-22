"""语义缓存的越权与脱敏边界。

缓存按问题相似度命中，命中者未必是当初提问的那个人，所以"密级"之外还
要单独判"脱敏"。这批用例锁的就是这条边界——它不是可选优化项。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from ecom_copilot.enterprise import semantic_cache as sc  # noqa: E402
from ecom_copilot.enterprise.semantic_cache import SemanticCache  # noqa: E402

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
