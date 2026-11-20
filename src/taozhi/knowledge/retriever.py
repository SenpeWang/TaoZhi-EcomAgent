"""授权候选内的不可变检索：BM25 + 本地字符向量 + 语义向量 + HyDE，禁止先全局检索再隐藏。

三路/四路信号各自排序后用倒数排名融合（RRF，k=60），任何一路失效只收窄召回面：
- BM25 路由 Elasticsearch 承担（中文 ik 分词，标题+正文检索，权限过滤前置）；
- ANN 语义路由 PostgreSQL pgvector 承担（chunks.embedding vector(512) + HNSW 近似近邻索引，权限过滤前置）；
- HyDE 向量同样直接参与第一阶段 `_pg_ann` 召回并进入 RRF 融合；
- ES 不可达：BM25 回落本地计算；PG 不可达：语义回落本地全量计算。自动降级不阻断问答。
- ANN 距离闸门：top-k 中无任何切片余弦距离 <= ann_max_distance 即判资料不足；无查询向量时闸门不生效。
- Rerank 精排：带文档标题送入本地交叉编码器 bge-reranker-v2-m3 重排，并结合版本/多实体策略微调排序。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from taozhi.domain.entity_extraction import extract_entities
from taozhi.domain.privacy import redact
from taozhi.domain.ranking import (
    apply_entity_diversity,
    apply_recency_tiebreak,
    chunk_title,
    dedup_chunks_by_text,
    tokenize_zh,
)
from taozhi.infrastructure.persistence.access_repo import can_read
from taozhi.observability.tracing import node_span
from ..config import Config, load_config
from ..knowledge.db import fetch_all, fetch_one
from ..knowledge.embedding import to_vector
from ..knowledge.index_cache import encode_index

RRF_K = 60
RRF_FLOOR = 0.0022
BACKFILL_LIMIT = 256
SEMANTIC_DIM = 512
GATE_MARGIN = 0.06

_HYDE_SYSTEM = (
    "你是商品资料检索助手。根据用户问题，写一段可能出现在商品资料库中的正文段落："
    "陈述句、含型号、参数或步骤等具体细节。允许内容与事实不符，禁止任何解释、前言或标题，只输出段落正文。"
)


def _ranks(scores):
    order = np.argsort(-scores)
    ranks = np.empty(len(scores), dtype=np.int64)
    ranks[order] = np.arange(1, len(scores) + 1)
    return ranks


def build_hyde_vector(
    query: str,
    *,
    conn: Any = None,
    p: Any = None,
    job: Optional[Dict[str, Any]] = None,
    trace_out: Optional[Dict[str, Any]] = None,
) -> Optional[np.ndarray]:
    """脱敏后生成假设性答案并编码为检索向量（生产与离线评测共用同一口径）。"""
    tr: Dict[str, Any] = {
        "used": False,
        "elapsed_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "llm_attempts": 0,
        "failover_count": 0,
        "outcome": "skipped",
    }
    try:
        cfg = load_config()
        if not cfg.hyde_enabled:
            return None
        if job is not None:
            if not (job.get("payload") or {}).get("allow_external", True) or int(job.get("input_level", 1)) >= 3:
                return None
            if conn is not None:
                row = fetch_one(conn, "SELECT count(*) AS n FROM model_calls WHERE job_id=%s", (job["id"],))
                if row and row["n"] >= cfg.max_model_calls:
                    return None
        from ..ai.client import LLMClient, ModelTier
        from ..ai.observability import usage_scope
        from ..knowledge.embedding import available as embed_available, encode_passages

        if not embed_available():
            return None
        started = time.perf_counter()
        outcome = "ok"
        text = ""
        hyde_max_tok = int(getattr(cfg, "hyde_max_tokens", Config.hyde_max_tokens))
        hyde_chars = int(getattr(cfg, "hyde_passage_chars", Config.hyde_passage_chars))
        with usage_scope() as hyde_usage:
            try:
                text = LLMClient(allow_overseas=False).chat(
                    redact(query),
                    tier=ModelTier.FAST,
                    system=_HYDE_SYSTEM,
                    max_tokens=hyde_max_tok,
                )
            except Exception:
                outcome = "failed"
                raise
            finally:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                u = hyde_usage.to_dict()
                tr.update(
                    used=outcome == "ok",
                    elapsed_ms=elapsed_ms,
                    prompt_tokens=u["prompt_tokens"],
                    completion_tokens=u["completion_tokens"],
                    total_tokens=u["total_tokens"],
                    cost_usd=u["cost_usd"],
                    llm_attempts=u["llm_attempts"],
                    failover_count=u["failover_count"],
                    outcome=outcome,
                )
                if conn is not None and p is not None and job is not None:
                    try:
                        conn.execute(
                            "INSERT INTO model_calls(tenant_id,job_id,role,elapsed_ms,prompt_tokens,completion_tokens,outcome) "
                            "VALUES(%s,%s,'hyde',%s,%s,%s,%s)",
                            (p.tenant_id, job["id"], elapsed_ms, u["prompt_tokens"], u["completion_tokens"], outcome),
                        )
                    except Exception:
                        pass
        if not text or len(text.strip()) < 20:
            tr["outcome"] = "short_or_empty"
            return None
        return encode_passages([text.strip()[:hyde_chars]])[0]
    except Exception:
        return None
    finally:
        if trace_out is not None:
            trace_out.update(tr)


def _semantic_matrix(conn, tenant_id, chunks, dim):
    """返回与 chunks 对齐的向量矩阵及存在标记；缺失向量补算并写缓存，失败返回 None。"""
    try:
        mats = [to_vector(c.get("embedding")) for c in chunks]
        mats = [v if (v is not None and len(v) == dim) else None for v in mats]
        missing = [i for i, v in enumerate(mats) if v is None]
        if missing:
            picked = missing[:BACKFILL_LIMIT]
            vecs = encode_index(
                conn,
                tenant_id,
                [(chunks[i].get("doc_title"), chunks[i]["text"]) for i in picked],
            )
            if vecs is None:
                return None
            for i, v in zip(picked, vecs):
                mats[i] = np.asarray(v, dtype="float32")
                try:
                    from ..knowledge.db import database_connection

                    with database_connection() as bc:
                        bc.execute("UPDATE chunks SET embedding=%s WHERE id=%s", (mats[i], chunks[i]["id"]))
                except Exception:
                    pass
        rows = [m for m in mats if m is not None]
        if not rows:
            return None
        return np.stack(rows), [m is not None for m in mats]
    except Exception:
        return None


def _es_rank_signal(rank_map, chunks, extra_rank=None):
    """把排名表转成与 chunks 对齐的 RRF 信号分；未上榜的排在末尾。"""
    n = extra_rank if extra_rank is not None else len(chunks) + 1
    out = np.full(len(chunks), 1.0 / (RRF_K + min(n, len(chunks) + 1)))
    for i, c in enumerate(chunks):
        if c["id"] in rank_map:
            out[i] = 1.0 / (RRF_K + rank_map[c["id"]])
    return out


def _rerank_text(title: str, text: str, max_chars: int) -> str:
    """将文档标题附于正文之后再精排：补齐无标题切片归属信号的同时不稀释正文开头注意力。"""
    t = (title or "").strip()
    if not t:
        return text[:max_chars]
    suffix = f"\n{t}"
    if len(suffix) >= max_chars:
        return text[:max_chars]
    return f"{text[: max_chars - len(suffix)]}{suffix}"


def _rerank_order(query, chunks, fused, allowed=None, trace_out: Optional[Dict[str, Any]] = None):
    """RRF 融合后的候选精排，返回 [(下标, 精排分或 None)]，输入附带文档标题以消除盲区。"""
    cfg = load_config()
    fused_order = [int(i) for i in np.argsort(-fused) if fused[i] >= RRF_FLOOR]
    if not fused_order:
        fused_order = [int(i) for i in np.argsort(-fused)]
    if not cfg.rerank_enabled or len(fused_order) < 2:
        if trace_out is not None:
            trace_out.update(rerank_ms=0, rerank_used=False, rerank_fallback=False)
        return [(i, None) for i in fused_order]
    cand = fused_order[: cfg.rerank_candidates]
    t0 = time.perf_counter()
    try:
        from ..knowledge.embedding import rerank as rerank_client

        rerank_texts = [
            _rerank_text(chunk_title(chunks[i], allowed), chunks[i]["text"], cfg.rerank_chars)
            for i in cand
        ]
        scores = np.asarray(rerank_client(query, rerank_texts), dtype="float32")
        if trace_out is not None:
            trace_out.update(rerank_ms=int((time.perf_counter() - t0) * 1000), rerank_used=True, rerank_fallback=False)
        return [(cand[j], float(scores[j])) for j in np.argsort(-scores)]
    except Exception as exc:
        _LOG.warning("Rerank 失败，保持融合序 (%s): %s", type(exc).__name__, exc)
        if trace_out is not None:
            trace_out.update(rerank_ms=int((time.perf_counter() - t0) * 1000), rerank_used=False, rerank_fallback=True)
        return [(i, None) for i in cand]


def _finalize(query, chunks, fused, limit, allowed, p, trace_out: Optional[Dict[str, Any]] = None):
    """结果装配：带标题精排 → 低分过滤 → 版本/多实体排序校正 → 同文档限流 → 超长截断。"""
    cfg = load_config()
    ranked = _rerank_order(query, chunks, fused, allowed=allowed, trace_out=trace_out)
    top = next((s for _, s in ranked if s is not None), None)
    if top is not None:
        floor = max(cfg.rerank_min_score, top * cfg.rerank_relative)
        kept = [(i, s) for i, s in ranked if s is not None and s >= floor]
        ranked = kept or ranked[:1]

    ranked = apply_recency_tiebreak(query, ranked, chunks, allowed, float(getattr(cfg, "recency_boost", 0.08)))
    ranked = apply_entity_diversity(
        query, ranked, chunks, allowed, limit, cfg.max_chunks_per_doc, int(getattr(cfg, "entity_quota", 2))
    )

    per_doc: Dict[str, int] = {}
    result = []
    for i, score in ranked:
        if len(result) >= limit:
            break
        c = chunks[int(i)]
        d = allowed.get(c["document_id"])
        if d is None:
            continue
        if per_doc.get(d["id"], 0) >= cfg.max_chunks_per_doc:
            continue
        per_doc[d["id"]] = per_doc.get(d["id"], 0) + 1
        text = c["text"] if p.boss else redact(c["text"])
        truncated = len(text) > cfg.quote_max_chars
        if truncated:
            text = text[: cfg.quote_max_chars] + "…（正文过长已截断）"
        result.append(
            dict(
                document_id=d["id"],
                version=c["version"],
                acl_version=d["acl_version"],
                chunk_id=c["id"],
                title=d["title"],
                quote=text,
                page=c["page"],
                level=d["level"],
                node_id=d["node_id"],
                tenant_id=p.tenant_id,
                ai_allowed=d["ai_allowed"],
                score=round(float(fused[i]), 4),
                kind="document",
                rerank_score=None if score is None else round(score, 4),
                truncated=truncated,
            )
        )
    return result


def _ann_gate(chunks, vecs, qv, max_dist):
    """ANN 距离闸门（两级判定）：先判有没有相关证据，再裁偏离过远的尾部。"""
    if qv is None:
        return chunks, vecs
    q = np.asarray(qv, dtype="float32").reshape(-1)

    def _d(v):
        return None if (v is None or len(v) != SEMANTIC_DIM) else 1.0 - float(v @ q)

    dists = [_d(v) for v in vecs]
    known = [d for d in dists if d is not None]
    if not known:
        return chunks, vecs
    if min(known) > max_dist:
        return [], []
    ceiling = max(max_dist, min(known) + GATE_MARGIN)
    keep = [i for i, d in enumerate(dists) if d is None or d <= ceiling]
    return [chunks[i] for i in keep], [vecs[i] for i in keep]


def _pg_ann(conn, tenant_id, allowed_ids, qv, size=200):
    """PG pgvector ANN（HNSW 近似近邻）：授权文档当前版本内按余弦距离取前 size。"""
    rows = fetch_all(
        conn,
        """SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version
        WHERE c.tenant_id=%s AND c.document_id=ANY(%s) AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> %s::vector LIMIT %s""",
        (tenant_id, list(allowed_ids), qv, size),
    )
    return {r["id"]: rank for rank, r in enumerate(rows, 1)}


def _mention_chunk_ids(conn, tenant_id: str, allowed_ids: list, entities: List[str], quota: int) -> List[str]:
    """按提问中的多实体查询 knowledge_mentions，补充高概率专属文档切片进入第一阶段候选。"""
    if len(entities) < 2 or not allowed_ids or quota <= 0:
        return []
    out: List[str] = []
    for ent in entities:
        rows = fetch_all(
            conn,
            "SELECT f.chunk_id FROM knowledge_mentions f "
            "JOIN documents d ON d.id=f.document_id AND d.current_version=f.version "
            "WHERE f.tenant_id=%s AND f.document_id=ANY(%s) AND UPPER(f.entity)=%s LIMIT %s",
            (tenant_id, allowed_ids, ent.upper(), quota),
        )
        for r in rows:
            if r["chunk_id"] not in out:
                out.append(r["chunk_id"])
    return out


def _lexical_signals(query: str, chunks: list, tfidf_features: int) -> tuple:
    """共享词法信号计算（词重叠率 + 字符 n-gram TF-IDF 余弦 + 分词列表），消除快慢路径重复逻辑。"""
    tokens = [tokenize_zh(c["text"]) for c in chunks]
    terms = tokenize_zh(query)
    termset = set(terms)
    overlap = np.asarray([len(termset.intersection(ts)) / max(1, len(termset)) for ts in tokens])
    try:
        model = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), max_features=tfidf_features)
        vectors = model.fit_transform([c["text"] for c in chunks])
        dense = cosine_similarity(model.transform([query]), vectors).reshape(-1)
    except ValueError:
        dense = np.zeros(len(chunks))
    return tokens, terms, overlap, dense


def search_documents(
    conn,
    p,
    query,
    limit=8,
    hyde_vector=None,
    trace_out: Optional[Dict[str, Any]] = None,
    doc_id_prefix: Optional[str] = None,
):
    """授权文档集内执行混合检索（ES BM25 + PG pgvector + 词重叠 + 字符 TF-IDF + HyDE）并精排返回。"""
    with node_span("knowledge.search", "RETRIEVER") as span:
        documents = _search_authorized_documents(conn, p, query, limit, hyde_vector, trace_out, doc_id_prefix)
        if span is not None:
            span.set_attribute("retrieval.result_count", len(documents))
        return documents


def _search_authorized_documents(conn, p, query, limit, hyde_vector, trace_out, doc_id_prefix):
    t_total = time.perf_counter()
    tr: Dict[str, Any] = {
        "embed_ms": 0,
        "es_ms": 0,
        "pg_ann_ms": 0,
        "hyde_ann_ms": 0,
        "rerank_ms": 0,
        "total_ms": 0,
        "es_used": False,
        "es_fallback": False,
        "rerank_used": False,
        "rerank_fallback": False,
        "ann_blocked": False,
    }
    try:
        docs = fetch_all(conn, "SELECT * FROM documents WHERE tenant_id=%s AND state='published'", (p.tenant_id,))
        allowed = {
            d["id"]: d
            for d in docs
            if can_read(conn, p, d) and (not doc_id_prefix or str(d["id"]).startswith(doc_id_prefix))
        }
        if not allowed:
            return []
        qv = None
        bm25_rank = None
        ann_rank = None
        hyde_rank = None
        t_emb = time.perf_counter()
        try:
            from ..knowledge.embedding import available as embed_available, encode_query

            if embed_available():
                qv = encode_query(query)
        except Exception:
            qv = None
        tr["embed_ms"] = int((time.perf_counter() - t_emb) * 1000)

        t_es = time.perf_counter()
        try:
            from ..knowledge import es_index

            if es_index.available():
                bm25_rank = es_index.search_bm25(p.tenant_id, list(allowed), query)
                tr["es_used"] = True
            else:
                tr["es_fallback"] = True
        except Exception as exc:
            _LOG.warning("ES BM25 召回失败，回落本地词法 (%s): %s", type(exc).__name__, exc)
            bm25_rank = None
            tr["es_fallback"] = True
        tr["es_ms"] = int((time.perf_counter() - t_es) * 1000)

        if qv is not None:
            t_ann = time.perf_counter()
            try:
                ann_rank = _pg_ann(conn, p.tenant_id, list(allowed), np.asarray(qv, dtype="float32").reshape(-1))
            except Exception as exc:
                _LOG.warning("PG ANN 召回失败，该路缺席 (%s): %s", type(exc).__name__, exc)
                ann_rank = None
            tr["pg_ann_ms"] = int((time.perf_counter() - t_ann) * 1000)

        if hyde_vector is not None:
            t_hy = time.perf_counter()
            try:
                hyde_rank = _pg_ann(conn, p.tenant_id, list(allowed), np.asarray(hyde_vector, dtype="float32").reshape(-1))
            except Exception:
                hyde_rank = None
            tr["hyde_ann_ms"] = int((time.perf_counter() - t_hy) * 1000)

        if bm25_rank or ann_rank or hyde_rank:
            entities = extract_entities(query)
            mention_ids = _mention_chunk_ids(
                conn, p.tenant_id, list(allowed), entities, int(getattr(load_config(), "entity_quota", 2))
            )
            ids = list(dict.fromkeys(list(bm25_rank or {}) + list(ann_rank or {}) + list(hyde_rank or {}) + mention_ids))
            chunks = [
                c
                for c in fetch_all(
                    conn,
                    "SELECT c.*, d.title AS doc_title FROM chunks c "
                    "JOIN documents d ON d.id=c.document_id AND d.current_version=c.version "
                    "WHERE c.tenant_id=%s AND c.document_id=ANY(%s) AND c.id=ANY(%s) "
                    "ORDER BY c.document_id, c.ordinal",
                    (p.tenant_id, list(allowed), ids),
                )
                if c["id"] in set(ids)
            ]
            if not chunks:
                return []
            chunks = dedup_chunks_by_text(chunks)
            vecs = [to_vector(c.get("embedding")) for c in chunks]
            pre_gate_n = len(chunks)
            chunks, vecs = _ann_gate(chunks, vecs, qv, load_config().ann_max_distance)
            if pre_gate_n > 0 and not chunks:
                tr["ann_blocked"] = True
                return []
            tfidf_cap = int(getattr(load_config(), "tfidf_max_features", Config.tfidf_max_features))
            _, _, overlap, dense = _lexical_signals(query, chunks, tfidf_cap)
            signals = [_es_rank_signal(bm25_rank or {}, chunks), _es_rank_signal(ann_rank or {}, chunks), overlap, dense]
            if hyde_rank:
                signals.append(_es_rank_signal(hyde_rank, chunks))
            else:
                present = [v is not None and len(v) == SEMANTIC_DIM for v in vecs]
                if any(present) and hyde_vector is not None:
                    matrix = np.stack([v for v, ok in zip(vecs, present) if ok])
                    idx = [i for i, ok in enumerate(present) if ok]
                    hy = np.zeros(len(chunks))
                    hy[idx] = cosine_similarity(matrix, np.asarray(hyde_vector, dtype="float32").reshape(1, -1)).reshape(-1)
                    signals.append(hy)
            fused = np.zeros(len(chunks))
            for s in signals:
                if np.max(s) > np.min(s):
                    fused += 1.0 / (RRF_K + _ranks(s))
            return _finalize(query, chunks, fused, limit, allowed, p, trace_out=tr)

        chunks = fetch_all(
            conn,
            "SELECT c.*, d.title AS doc_title FROM chunks c "
            "JOIN documents d ON d.id=c.document_id AND d.current_version=c.version "
            "WHERE c.tenant_id=%s AND c.document_id=ANY(%s) ORDER BY c.document_id, c.ordinal LIMIT 20000",
            (p.tenant_id, list(allowed)),
        )
        if not chunks:
            return []
        chunks = dedup_chunks_by_text(chunks)
        if qv is not None:
            vecs = [to_vector(c.get("embedding")) for c in chunks]
            pre_gate_n = len(chunks)
            chunks, vecs = _ann_gate(chunks, vecs, qv, load_config().ann_max_distance)
            if pre_gate_n > 0 and not chunks:
                tr["ann_blocked"] = True
                return []
        tfidf_cap = int(getattr(load_config(), "tfidf_max_features", Config.tfidf_max_features))
        tokens, terms, overlap, dense = _lexical_signals(query, chunks, tfidf_cap)
        scores = np.maximum(np.asarray(BM25Okapi(tokens).get_scores(terms)), 0)
        signals = [scores, dense, overlap]
        if qv is not None:
            try:
                packed = _semantic_matrix(conn, p.tenant_id, chunks, qv.shape[1])
                if packed is not None:
                    matrix, present = packed
                    idx = [i for i, ok in enumerate(present) if ok]
                    sem = np.zeros(len(chunks))
                    sem[idx] = cosine_similarity(matrix, qv).reshape(-1)
                    signals.append(sem)
                    if hyde_vector is not None:
                        hy = np.zeros(len(chunks))
                        hy[idx] = cosine_similarity(matrix, np.asarray(hyde_vector, dtype="float32").reshape(1, -1)).reshape(-1)
                        signals.append(hy)
            except Exception as exc:
                _LOG.warning("语义向量矩阵评分失败，已降级为 BM25+TF-IDF+词面信号: %s", exc)
        fused = np.zeros(len(chunks))
        for s in signals:
            if np.max(s) > np.min(s):
                fused += 1.0 / (RRF_K + _ranks(s))
        return _finalize(query, chunks, fused, limit, allowed, p, trace_out=tr)
    finally:
        tr["total_ms"] = int((time.perf_counter() - t_total) * 1000)
        if trace_out is not None:
            trace_out.update(tr)
