"""检索层单元测试：证据融合、重试与检索适配。"""



import numpy as np

from taozhi.infrastructure.knowledge import embedding as ent_embedding
from taozhi.infrastructure.knowledge import retriever as ent_retriever
from taozhi.domain.ranking import fuse_evidence_rounds


def test_retry_can_replace_weak_evidence_when_the_first_round_is_full():
    """首轮满额时补查头部可进入最终引用，同块两轮命中只返回一份。"""
    first = [{"chunk_id": f"first-{i}", "version": 1} for i in range(10)]
    fresh = {"chunk_id": "new", "version": 2}
    second = [fresh, first[-1], first[-1]]
    merged = fuse_evidence_rounds(first, second, 10)
    assert len(merged) == 10
    assert merged[:3] == [first[-1], first[0], fresh]
    assert first[-2] not in merged and merged.count(first[-1]) == 1
    assert fuse_evidence_rounds(first, [], 10) == first


class _Cfg:
    """检索配置桩，提供精排与召回相关字段。"""

    def __init__(self, enabled=True, candidates=20, rerank_chars=1000,
                 max_chunks_per_doc=3, rerank_min_score=0.05, rerank_relative=0.15,
                 quote_max_chars=1200, ann_max_distance=0.45):
        self.rerank_enabled = enabled
        self.es_enabled = True
        self.rerank_candidates = candidates
        self.rerank_chars = rerank_chars
        self.max_chunks_per_doc = max_chunks_per_doc
        self.rerank_min_score = rerank_min_score
        self.rerank_relative = rerank_relative
        self.quote_max_chars = quote_max_chars
        self.ann_max_distance = ann_max_distance


def _fused_chunks(n=5):
    """构造融合检索候选切片与分数。

    Args:
        n: 切片数量。
    """
    chunks = [{"id": f"k{i}", "text": f"切片{i} 膜切机参数"} for i in range(n)]
    fused = np.linspace(0.01, 0.03, n)
    return chunks, fused


def test_enterprise_rerank_reorders_candidates(monkeypatch):
    """验证企业检索精排会重排候选并保持分数降序。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    monkeypatch.setattr(ent_embedding, "rerank",
                        lambda q, texts: [float(len(texts) - j) for j in range(len(texts))])
    ranked = ent_retriever._rerank_order("膜切机", chunks, fused)
    order = [i for i, _ in ranked]
    scores = [s for _, s in ranked]
    assert order[0] == 4 and order[-1] == 0
    assert sorted(order) == list(range(5))
    assert scores == sorted(scores, reverse=True)


def test_enterprise_rerank_failure_falls_back_to_fused(monkeypatch):
    """验证精排失败时回退融合顺序且不标记分数。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())

    def _boom(q, texts):
        """模拟精排服务不可用。

        Args:
            q: 查询文本。
            texts: 待处理文本列表。
        """
        raise RuntimeError("服务不可用")

    monkeypatch.setattr(ent_embedding, "rerank", _boom)
    ranked = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert [i for i, _ in ranked] == [4, 3, 2, 1, 0]
    assert all(s is None for _, s in ranked)


def test_enterprise_rerank_disabled_keeps_fused_order(monkeypatch):
    """验证关闭精排时保持融合顺序且不调用客户端。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(enabled=False))

    def _unexpected(q, texts):
        """断言关闭精排时不应被调用的替身。

        Args:
            q: 查询文本。
            texts: 待处理文本列表。
        """
        raise AssertionError("关闭精排时不应调用客户端")

    monkeypatch.setattr(ent_embedding, "rerank", _unexpected)
    ranked = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert [i for i, _ in ranked] == [4, 3, 2, 1, 0]


def test_enterprise_rerank_floor_and_candidates(monkeypatch):
    """验证精排只处理配置数量的候选并按阈值过滤。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
    """
    chunks, fused = _fused_chunks()
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(candidates=2))
    seen = {}

    def _fake(q, texts):
        """替身精排函数，记录文本并返回固定分数。

        Args:
            q: 查询文本。
            texts: 待处理文本列表。
        """
        seen["texts"] = texts
        return [0.9, 0.1]

    monkeypatch.setattr(ent_embedding, "rerank", _fake)
    ranked = ent_retriever._rerank_order("膜切机", chunks, fused)
    assert [i for i, _ in ranked] == [4, 3]
    assert len(seen["texts"]) == 2
    low = np.array([0.0001] * 5)
    ranked = ent_retriever._rerank_order("膜切机", chunks, low)
    assert [i for i, _ in ranked] == [0, 1]



class _P:
    """检索权限主体桩，提供租户与老板标志。"""

    def __init__(self, boss=True):
        self.tenant_id = "t1"
        self.boss = boss


def _mk_chunks(specs):
    """specs: [(text, document_id)] → 与 _finalize 期望一致的切片字典。"""
    return [{"id": f"k{i}", "text": t, "document_id": d, "version": 1, "page": 1}
            for i, (t, d) in enumerate(specs)]


def _mk_allowed(ids):
    """构造允许访问的文档映射。

    Args:
        ids: 标识集合。
    """
    return {d: {"id": d, "acl_version": 1, "title": f"文档{d}", "level": 1,
                "node_id": "n1", "ai_allowed": True} for d in ids}


def test_enterprise_rerank_low_score_filtered(monkeypatch):
    """远落后于首条的候选（此处 0.02/0.01）被剔除，且不至于把结果清空。"""
    chunks = _mk_chunks([(f"切片{i} 膜切机参数", "d1") for i in range(4)])
    fused = np.linspace(0.01, 0.04, 4)
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.9, 0.8, 0.02, 0.01])
    out = ent_retriever._finalize("膜切机", chunks, fused, 10, _mk_allowed(["d1"]), _P())
    assert [r["rerank_score"] for r in out] == [0.9, 0.8]


def test_enterprise_finalize_caps_chunks_per_document(monkeypatch):
    """同一文档最多 3 条：避免 top-k 被单篇文档占满，保证证据多样性。"""
    specs = [(f"切片{i}", "d1") for i in range(6)] + [("切片X", "d2")]
    chunks = _mk_chunks(specs)
    fused = np.linspace(0.02, 0.01, len(chunks))
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(max_chunks_per_doc=3))
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.5] * len(texts))
    out = ent_retriever._finalize("膜切机", chunks, fused, 10, _mk_allowed(["d1", "d2"]), _P())
    assert sum(r["document_id"] == "d1" for r in out) == 3
    assert sum(r["document_id"] == "d2" for r in out) == 1


def test_enterprise_finalize_truncates_overlong_quote(monkeypatch):
    """超过字符预算的正文必须截断并标记，防止撑爆上下文与精排。"""
    long_text = "钢化膜" * 500
    chunks = _mk_chunks([(long_text, "d1")])
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(quote_max_chars=120))
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.5])
    out = ent_retriever._finalize("膜切机", chunks, np.array([0.02]), 10, _mk_allowed(["d1"]), _P())
    assert out[0]["truncated"] is True
    assert len(out[0]["quote"]) == 120 + len("…（正文过长已截断）")


def test_enterprise_dedup_removes_identical_text():
    """归一化后完全相同的切片只保留排序靠前的一条。"""
    from taozhi.domain.ranking import dedup_chunks_by_text

    chunks = _mk_chunks([("同一段正文", "d1"), ("同一 段\n正文", "d1"), ("另一段正文", "d2")])
    kept = dedup_chunks_by_text(chunks)
    assert [c["id"] for c in kept] == ["k0", "k2"]


def test_rerank_includes_document_title(monkeypatch):
    """精排送入交叉编码器的文本应携带文档标题，避免标题写型号正文写参数被错杀。"""
    chunks = [{"id": "k0", "text": "功率 65W", "doc_title": "LX-20 规格书", "document_id": "d1", "version": 1, "page": 1}]
    chunks.append({"id": "k1", "text": "功率 140W", "document_id": "d2", "version": 1, "page": 1})
    fused = np.array([0.03, 0.02])
    captured = {}

    def _fake_rerank(q, texts):
        """替身精排函数，记录文本并返回固定分数。

        Args:
            q: 查询文本。
            texts: 待处理文本列表。
        """
        captured["texts"] = list(texts)
        return [0.9, 0.2]

    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    monkeypatch.setattr(ent_embedding, "rerank", _fake_rerank)
    ent_retriever._rerank_order("LX-20 功率", chunks, fused, allowed=_mk_allowed(["d1", "d2"]))
    assert captured["texts"][0] == "功率 65W\nLX-20 规格书"
    assert captured["texts"][1] == "功率 140W\n文档d2"


def test_recency_tiebreak_and_entity_diversity(monkeypatch):
    """同系列多版本非历史问句优先最新版本；多实体对比问句保底覆盖各个提及实体。"""
    chunks = [
        {"id": "k_v1", "text": "整机保修 12 个月", "doc_title": "保修政策 V1.0（2025-03-01 生效）", "document_id": "d_v1", "version": 1, "page": 1},
        {"id": "k_v2", "text": "整机保修 18 个月", "doc_title": "保修政策 V2.0（2026-09-01 生效）", "document_id": "d_v2", "version": 1, "page": 1},
    ]
    allowed = {
        "d_v1": {"id": "d_v1", "acl_version": 1, "title": chunks[0]["doc_title"], "level": 1, "node_id": "n1", "ai_allowed": True},
        "d_v2": {"id": "d_v2", "acl_version": 1, "title": chunks[1]["doc_title"], "level": 1, "node_id": "n1", "ai_allowed": True},
    }
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.88, 0.85])
    out_cur = ent_retriever._finalize("整机保修期现在是多久？", chunks, np.array([0.03, 0.03]), 5, allowed, _P())
    assert out_cur[0]["document_id"] == "d_v2"
    out_hist = ent_retriever._finalize("保修政策什么时候改的？", chunks, np.array([0.03, 0.03]), 5, allowed, _P())
    assert out_hist[0]["document_id"] == "d_v1"

    cmp_chunks = [
        {"id": "c0", "text": "LX-21 第一段 140W", "doc_title": "LX-21 手册", "document_id": "d21", "version": 1, "page": 1},
        {"id": "c1", "text": "LX-21 第二段 三C口", "doc_title": "LX-21 手册", "document_id": "d21", "version": 1, "page": 2},
        {"id": "c2", "text": "LX-30 参数 120W", "doc_title": "LX-30 规格表", "document_id": "d30", "version": 1, "page": 1},
    ]
    cmp_allowed = {
        "d21": {"id": "d21", "acl_version": 1, "title": "LX-21 手册", "level": 1, "node_id": "n1", "ai_allowed": True},
        "d30": {"id": "d30", "acl_version": 1, "title": "LX-30 规格表", "level": 1, "node_id": "n1", "ai_allowed": True},
    }
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.95, 0.90, 0.82])
    cmp_out = ent_retriever._finalize("LX-30 和 LX-21 哪个功率高？", cmp_chunks, np.array([0.03, 0.025, 0.02]), 2, cmp_allowed, _P())
    assert {r["document_id"] for r in cmp_out} == {"d21", "d30"}


def test_search_documents_fast_path_runs_hyde_pg_ann_and_title_rerank(monkeypatch):
    """ES/PG 快道中 hyde_vector 应真实参与 _pg_ann 召回，且精选切片带标题送入 rerank。"""
    from taozhi.infrastructure.knowledge import es_index

    docs = [{"id": "d1", "title": "LX-30 规格", "scope": "company", "level": 1, "node_id": "n1", "ai_allowed": True, "acl_version": 1}]
    chunks = [{"id": "c_hyde", "tenant_id": "t1", "document_id": "d1", "version": 1, "ordinal": 0, "page": 1, "text": "120W 双 C 口", "doc_title": "LX-30 规格", "embedding": None}]
    ann_calls = []

    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg())
    monkeypatch.setattr(ent_retriever, "readable_published_documents", lambda conn, p, prefix: {"d1": docs[0]})
    monkeypatch.setattr(es_index, "search_bm25", lambda tid, allowed, q: {})
    monkeypatch.setattr(ent_embedding, "available", lambda: True)
    monkeypatch.setattr(ent_embedding, "encode_query", lambda q: np.ones((1, 512), dtype="float32"))

    def _fake_pg_ann(conn, tid, allowed_ids, vec, size=200):
        """替身 PG 向量检索，按向量首值决定是否返回 HyDE 命中。

        Args:
            conn: 数据库连接。
            tid: 租户标识。
            allowed_ids: 允许访问的文档标识集合。
            vec: 查询向量。
            size: 返回数量上限。
        """
        ann_calls.append(float(vec[0]))
        return {"c_hyde": 1} if float(vec[0]) > 1.5 else {}

    monkeypatch.setattr(ent_retriever, "_pg_ann", _fake_pg_ann)

    def _fake_fetch_all(conn, sql, params=()):
        """替身 SQL 查询，按语句返回切片或空结果。

        Args:
            conn: 数据库连接。
            sql: 要执行的 SQL 语句。
            params: SQL 绑定参数。
        """
        if "FROM chunks" in sql:
            return chunks
        return []

    monkeypatch.setattr(ent_retriever, "fetch_all", _fake_fetch_all)
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.9])

    hyde_vec = np.full(512, 2.0, dtype="float32")
    from taozhi.observability import tracing

    snapshots = []
    monkeypatch.setattr(tracing, "submit_span", snapshots.append)
    monkeypatch.setenv("TAOZHI_TRACING", "1")
    with tracing.job_trace("t1", "job", 1, "task.ask"):
        out = ent_retriever.search_documents(None, _P(), "120W 充电器", limit=5, hyde_vector=hyde_vec)
    search, = [s for s in snapshots if s["kind"] == "RETRIEVER" and s["status"] == "ok"]
    assert search["name"] == "knowledge.search" and search["attributes"]["retrieval.result_count"] == len(out)
    assert "120W 充电器" not in str(snapshots)
    assert len(ann_calls) == 2
    assert len(out) == 1 and out[0]["chunk_id"] == "c_hyde"
    assert out[0]["title"] == "LX-30 规格"


def test_ingest_split_preserves_markdown_table_blocks():
    """结构化切片在预算内不得把表格行从中间劈断。"""
    from taozhi.infrastructure.knowledge.ingest import split

    doc = (
        "# 产品对比\n\n"
        "| 型号 | 功率 | 接口 |\n"
        "| --- | --- | --- |\n"
        "| LX-20 | 65W | 2C1A |\n"
        "| LX-30 | 120W | 2C |\n\n"
        + ("补充说明段。" * 40)
    )
    chunks = split([(1, doc)], "doc_tbl", 1, max_chars=260, overlap=60)
    assert len(chunks) >= 2
    assert "| LX-20 | 65W | 2C1A |\n| LX-30 | 120W | 2C |" in chunks[0]["text"]


def test_entity_diversity_prefers_title_dedicated_documents(monkeypatch):
    """多实体保底应优先保送标题命中实体的专属文档，不被通用综述文档挤占。"""
    chunks = [
        {"id": "g0", "text": "培训手册提到 C1 与 C5 贴膜销售话术", "doc_title": "客服上岗培训手册", "document_id": "d_gen", "version": 1, "page": 1},
        {"id": "c1", "text": "直板机屏幕保护 0.33mm", "doc_title": "C1 钢化膜（评测产品资料）", "document_id": "d_c1", "version": 1, "page": 1},
        {"id": "c5", "text": "折叠屏内屏专用", "doc_title": "C5 全胶陶瓷膜（评测产品资料）", "document_id": "d_c5", "version": 1, "page": 1},
    ]
    allowed = {
        "d_gen": {"id": "d_gen", "acl_version": 1, "title": "客服上岗培训手册", "level": 1, "node_id": "n1", "ai_allowed": True},
        "d_c1": {"id": "d_c1", "acl_version": 1, "title": "C1 钢化膜（评测产品资料）", "level": 1, "node_id": "n1", "ai_allowed": True},
        "d_c5": {"id": "d_c5", "acl_version": 1, "title": "C5 全胶陶瓷膜（评测产品资料）", "level": 1, "node_id": "n1", "ai_allowed": True},
    }
    monkeypatch.setattr(ent_retriever, "load_config", lambda: _Cfg(max_chunks_per_doc=1))
    monkeypatch.setattr(ent_embedding, "rerank", lambda q, texts: [0.99, 0.85, 0.84])
    out = ent_retriever._finalize("iPhone 15 贴 C5 和 C1 怎么选？", chunks, np.array([0.04, 0.03, 0.02]), 2, allowed, _P())
    assert {r["document_id"] for r in out} == {"d_c1", "d_c5"}


def test_chunk_page_citations_and_numeric_release_order():
    """相同段落在多页分别保留页码，知识库多段版本按整数分量比较。"""
    from taozhi.domain.ranking import apply_recency_tiebreak, split_document_chunks

    chunks = split_document_chunks([(1, "本条有效"), (2, "有效")], "doc", 1, 128, 16)
    assert [(chunk["page"], chunk["text"]) for chunk in chunks] == [(1, "本条有效"), (2, "有效")]
    versions = [
        {"document_id": "previous", "doc_title": "保修政策 V1.9.2", "text": "旧条款"},
        {"document_id": "current", "doc_title": "保修政策 V1.10.12", "text": "新条款"},
    ]
    ranked = apply_recency_tiebreak("目前执行版本", [(0, 0.9), (1, 0.88)], versions, {}, 0.08)
    assert [index for index, _ in ranked] == [1, 0]
