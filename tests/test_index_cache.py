"""嵌入索引缓存：指纹命中就复用，未命中才调模型；口径变了必须重算。"""
import numpy as np
import pytest

from ecom_copilot.enterprise import embedding, index_cache
from ecom_copilot.enterprise.config import load_config
from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.embedding import embedding_input

DIM = 512


@pytest.fixture(autouse=True)
def _env():
    cfg = load_config()
    assert cfg.mode == "test" and cfg.dsn.endswith("/ecom_test")
    embedding.clear_query_cache()


def _stub(monkeypatch, calls):
    """替掉真实嵌入服务：记录每次请求，返回固定 512 维向量。"""
    def fake(texts, instruct=False):
        calls.append(list(texts))
        return np.tile(np.arange(DIM, dtype="float32"), (len(texts), 1))
    monkeypatch.setattr(index_cache, "model_signature", lambda: f"test-model:{DIM}")
    monkeypatch.setattr(index_cache, "encode_passages", fake)


def _stub_query(monkeypatch, calls, tag="test-model:512"):
    """替掉 _post：只用于验证查询向量是否命中缓存。

    返回值必须随输入变化——否则"不同问题"也会拿到同一个向量，
    断言 `not allclose` 就成了假失败。
    """
    def fake_post(texts, instruct=False):
        calls.append((list(texts), instruct))
        seed = float(abs(hash("|".join(texts))) % 900 + 1)
        return np.tile(np.arange(DIM, dtype="float32") + seed, (len(texts), 1))
    monkeypatch.setattr(embedding, "model_signature", lambda: tag)
    monkeypatch.setattr(embedding, "_post", fake_post)


def _count(conn, tenant):
    rows = fetch_all(conn, "SELECT count(*) AS n FROM embedding_cache WHERE tenant_id=%s",
                     (tenant,))
    return rows[0]["n"]


def test_digest_is_stable_and_title_sensitive():
    """指纹只由嵌入输入决定：同文本同指纹，换标题或换正文就变。"""
    a = embedding_input("规格表", "140W 三口")
    assert index_cache.digest(a) == index_cache.digest(a)
    assert index_cache.digest(a) != index_cache.digest(embedding_input("规格表2", "140W 三口"))
    assert index_cache.digest(a) != index_cache.digest(embedding_input("规格表", "140W 双口"))


def test_encode_index_reuses_cached_vectors(monkeypatch):
    """同文本第二次编码不该再走模型：请求次数停在第一次。"""
    calls = []
    _stub(monkeypatch, calls)
    tenant = "tenant_index_cache_a"
    pairs = [("规格表", "140W 三口"), ("手册", "保修一年")]
    with database_connection() as conn:
        first = index_cache.encode_index(conn, tenant, pairs)
        second = index_cache.encode_index(conn, tenant, pairs)
        cached = _count(conn, tenant)
        conn.execute("DELETE FROM embedding_cache WHERE tenant_id=%s", (tenant,))
    assert first.shape == second.shape == (2, DIM)
    assert np.allclose(first, second)
    assert len(calls) == 1 and len(calls[0]) == 2
    assert cached == 2


def test_encode_index_encodes_only_new_texts(monkeypatch):
    """同批里已缓存的切片跳过，只编码新增那条。"""
    calls = []
    _stub(monkeypatch, calls)
    tenant = "tenant_index_cache_b"
    with database_connection() as conn:
        index_cache.encode_index(conn, tenant, [("A", "text-1")])
        calls.clear()
        index_cache.encode_index(conn, tenant, [("A", "text-1"), ("A", "text-2")])
        conn.execute("DELETE FROM embedding_cache WHERE tenant_id=%s", (tenant,))
    assert len(calls) == 1
    assert calls[0] == ["A\ntext-2"]


def test_encode_index_skips_other_tenants_cache(monkeypatch):
    """缓存按租户隔离：另一租户的相同正文不能命中。"""
    calls = []
    _stub(monkeypatch, calls)
    with database_connection() as conn:
        index_cache.encode_index(conn, "tenant_index_cache_c", [("A", "same-text")])
        calls.clear()
        index_cache.encode_index(conn, "tenant_index_cache_d", [("A", "same-text")])
        conn.execute("DELETE FROM embedding_cache "
                     "WHERE tenant_id IN ('tenant_index_cache_c','tenant_index_cache_d')")
    assert len(calls) == 1


def test_encode_index_needs_embedding_service(monkeypatch):
    """服务不可用 → None。缓存不是依赖。"""
    monkeypatch.setattr(index_cache, "model_signature", lambda: "")
    with database_connection() as conn:
        assert index_cache.encode_index(conn, "tenant_index_cache_e", [("A", "x")]) is None


def test_encode_index_empty_pairs():
    with database_connection() as conn:
        assert index_cache.encode_index(conn, "tenant_index_cache_f", []) is None


def test_query_embedding_cache_hits(monkeypatch):
    """同一进程内同问只编码一次；换问题不算命中。"""
    calls = []
    _stub_query(monkeypatch, calls)
    first = embedding.encode_query("LX-20 多少钱")
    second = embedding.encode_query("LX-20 多少钱")
    other = embedding.encode_query("另一个问题")
    assert np.allclose(first, second)
    assert not np.allclose(first, other)
    assert len(calls) == 2
    assert all(instruct for _, instruct in calls)      # 查询必须走 instruct 前缀
    assert embedding.query_cache_stats()["hits"] == 1


def test_query_embedding_cache_evicts_oldest(monkeypatch):
    """LRU 有上限：超容量时淘汰最久未用的那条。"""
    monkeypatch.setattr(embedding, "_post",
                        lambda texts, instruct=False: np.tile(
                            np.arange(DIM, dtype="float32"), (len(texts), 1)))
    monkeypatch.setattr(embedding, "QUERY_CACHE_MAX", 3)
    calls = []
    for i in range(5):
        embedding.encode_query(f"问题{i}")
    embedding.encode_query("问题0")      # 把 0 提到最新
    embedding.encode_query("问题5")      # 被淘汰的应是 1
    assert embedding.query_cache_stats()["size"] == 3
    assert embedding.digest("问题1") not in " ".join(embedding._query_cache)
