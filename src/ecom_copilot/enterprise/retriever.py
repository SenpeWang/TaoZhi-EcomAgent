"""授权候选内的不可变检索：BM25 + 本地字符向量 + 语义向量，禁止先全局检索再隐藏。

三路信号各自排序后用倒数排名融合（RRF，k=60），任何一路失效只收窄召回面：
- BM25 路由 Elasticsearch 承担（中文 ik 分词，权限过滤前置）；
- ANN 语义路由 PostgreSQL pgvector 承担（chunks.embedding vector(512) + HNSW 近似近邻索引，权限过滤前置）；
- ES 不可达：BM25 回落本地计算；PG 不可达：语义回落本地全量计算。自动降级不阻断问答。
- ANN 距离闸门：top-k 中无任何切片余弦距离 ≤ ann_max_distance（默认 0.45，ECOM_ANN_MAX_DIST 可调）即判资料不足（返回空，由 pipeline 走"缺少可靠资料"分支）；无查询向量时闸门不生效。
- Rerank 精排：RRF 融合后取前 rerank_candidates（默认 20）条候选交由本地交叉编码器 bge-reranker-v2-m3
  （嵌入服务 /rerank）重排；服务不可用或失败时保持融合序，不阻断检索（ECOM_RERANK_ENABLED 可关闭）。
语义向量在入库时由 Worker 计算并写入 PG pgvector，存量切片在检索时惰性补算。
"""
import re,numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rank_bm25 import BM25Okapi
from .config import load_config
from .db import fetch_all
from .policy import can_read
from .privacy import redact

RRF_K=60
RRF_FLOOR=0.0022
BACKFILL_LIMIT=256
SEMANTIC_DIM=512

def words(text):
    import jieba
    return [x.casefold() for x in jieba.lcut(text) if x.strip() and (len(x)>1 or x.isalnum())]

def _ranks(scores):
    order=np.argsort(-scores);ranks=np.empty(len(scores),dtype=np.int64)
    ranks[order]=np.arange(1,len(scores)+1)
    return ranks

def _vec(raw):
    """把 chunks.embedding 读回的值统一成 numpy float32 数组；无法识别返回 None。

    读回形态：pgvector-python 注册后为 Vector 对象；未注册时为文本；历史 bytea 为字节串。
    """
    if raw is None:return None
    if isinstance(raw,np.ndarray):return raw.astype("float32",copy=False)
    to_numpy=getattr(raw,"to_numpy",None)
    if callable(to_numpy):
        try:return to_numpy().astype("float32",copy=False)
        except Exception:return None
    if isinstance(raw,(bytes,bytearray,memoryview)) and len(raw)%4==0:
        return np.frombuffer(raw,dtype="float32")
    return None

def _semantic_matrix(conn,chunks,dim):
    """返回与 chunks 对齐的向量矩阵及存在标记；缺失向量惰性补算（限流），失败返回 None。"""
    try:
        from .embedding import available as embed_available,encode_passages
        if not embed_available():return None
        mats=[None]*len(chunks);missing=[]
        for i,c in enumerate(chunks):
            v=_vec(c.get("embedding"))
            if v is not None and len(v)==dim:mats[i]=v
            else:missing.append(i)
        if missing:
            picked=missing[:BACKFILL_LIMIT]
            vecs=encode_passages([chunks[i]["text"] for i in picked])
            for i,v in zip(picked,vecs):
                mats[i]=v
                try:
                    # 独立连接写回：补算失败只损失增量向量，不污染调用方检索事务
                    from .db import database_connection
                    with database_connection() as bc:bc.execute("UPDATE chunks SET embedding=%s WHERE id=%s",(v,chunks[i]["id"]))
                except Exception:pass
        rows=[m for m in mats if m is not None]
        if not rows:return None
        return np.stack(rows),[m is not None for m in mats]
    except Exception:
        return None

def _es_rank_signal(rank_map,chunks,extra_rank=None):
    """把 ES 排名转成与 chunks 对齐的分数数组；未进 ES 前N的排最后（并列第 N+1）。"""
    n=extra_rank if extra_rank is not None else len(chunks)+1
    out=np.full(len(chunks),1.0/(RRF_K+min(n,len(chunks)+1)))
    for i,c in enumerate(chunks):
        if c["id"] in rank_map:out[i]=1.0/(RRF_K+rank_map[c["id"]])
    return out

def _rerank_order(query,chunks,fused):
    """RRF 融合后的候选精排：融合分达 RRF_FLOOR 的前 rerank_candidates 条交由本地
    交叉编码器（嵌入服务 /rerank）重排；服务不可用或失败时保持融合序，不阻断检索。"""
    cfg=load_config()
    fused_order=[int(i) for i in np.argsort(-fused) if fused[i]>=RRF_FLOOR]
    if not cfg.rerank_enabled or len(fused_order)<2:return fused_order
    cand=fused_order[:cfg.rerank_candidates]
    try:
        from .embedding import rerank as rerank_client
        scores=np.asarray(rerank_client(query,[chunks[i]["text"] for i in cand]),dtype="float32")
        return [cand[j] for j in np.argsort(-scores)]
    except Exception as exc:
        print("Rerank 失败，保持融合序",type(exc).__name__,flush=True)
        return cand

def _ann_gate(chunks,vecs,qv,max_dist):
    """ANN 距离闸门：剔除余弦距离超过 max_dist 的切片；无向量切片豁免（无法判定）。

    返回与 chunks 对齐的过滤结果；全部剔除时返回空列表，调用方按资料不足处理。
    距离口径与 pgvector `<=>` 一致（1 - 余弦相似度，两侧向量均已 L2 归一化）。
    """
    if qv is None:return chunks,vecs
    q=np.asarray(qv,dtype="float32").reshape(-1)
    keep=[i for i,v in enumerate(vecs) if v is None or len(v)!=SEMANTIC_DIM or 1.0-float(v@q)<=max_dist]
    return [chunks[i] for i in keep],[vecs[i] for i in keep]

def _pg_ann(conn,tenant_id,allowed_ids,qv,size=200):
    """PG pgvector ANN（HNSW 近似近邻）：授权文档当前版本内按余弦距离取前 size，返回 {chunk_id: rank}（rank 从 1 起）。"""
    rows=fetch_all(conn,"""SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version
        WHERE c.tenant_id=%s AND c.document_id=ANY(%s) AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> %s::vector LIMIT %s""",(tenant_id,list(allowed_ids),qv,size))
    return {r["id"]:rank for rank,r in enumerate(rows,1)}

def search_documents(conn,p,query,limit=8,hyde_vector=None):
    docs=fetch_all(conn,"SELECT * FROM documents WHERE tenant_id=%s AND state='published'",(p.tenant_id,))
    allowed={d["id"]:d for d in docs if can_read(conn,p,d)}
    if not allowed:return []
    qv=None
    bm25_rank=None
    ann_rank=None
    try:
        from .embedding import available as embed_available,encode_query
        if embed_available():qv=encode_query(query)
    except Exception:qv=None
    # BM25 走 ES（中文 ik 分词）、ANN 语义向量走 PG pgvector（HNSW），权限过滤均前置；任一路失败只收窄召回面
    try:
        from . import es_index
        if es_index.available():
            bm25_rank=es_index.search_bm25(p.tenant_id,list(allowed),query)
    except Exception as exc:
        print("ES BM25 召回失败，回落本地词法",type(exc).__name__,flush=True);bm25_rank=None
    if qv is not None:
        # qv 形状为 (1,dim)，pgvector dumper 只接受一维，必须先拉直
        try:ann_rank=_pg_ann(conn,p.tenant_id,list(allowed),np.asarray(qv,dtype="float32").reshape(-1))
        except Exception as exc:
            print("PG ANN 召回失败，该路缺席",type(exc).__name__,flush=True);ann_rank=None
    if bm25_rank or ann_rank:
        ids=list(dict.fromkeys(list(bm25_rank or {})+list(ann_rank or {})))
        chunks=[c for c in fetch_all(conn,"SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version WHERE c.tenant_id=%s AND c.document_id=ANY(%s) AND c.id=ANY(%s) ORDER BY c.document_id,c.ordinal",(p.tenant_id,list(allowed),ids)) if c["id"] in set(ids)]
        if not chunks:return []
        # ANN 相关性闸门：top-k 中无任何切片足够接近即判资料不足（无查询向量时闸门不生效）
        vecs=[_vec(c.get("embedding")) for c in chunks]
        chunks,vecs=_ann_gate(chunks,vecs,qv,load_config().ann_max_distance)
        if not chunks:return []
        signals=[_es_rank_signal(bm25_rank or {},chunks),_es_rank_signal(ann_rank or {},chunks)]
        tokens=[words(c["text"]) for c in chunks];terms=words(query);termset=set(terms)
        overlap=np.asarray([len(termset.intersection(ts))/max(1,len(termset)) for ts in tokens])
        signals.append(overlap)
        try:
            model=TfidfVectorizer(analyzer="char",ngram_range=(2,4),max_features=24000)
            vectors=model.fit_transform([c["text"] for c in chunks])
            dense=cosine_similarity(model.transform([query]),vectors).reshape(-1)
        except ValueError:dense=np.zeros(len(chunks))
        signals.append(dense)
        present=[v is not None and len(v)==SEMANTIC_DIM for v in vecs]
        if any(present) and qv is not None and hyde_vector is not None:
            # 语义相似度已由 ANN 路承担，这里只补 HyDE 第四路
            matrix=np.stack([v for v,ok in zip(vecs,present) if ok])
            idx=[i for i,ok in enumerate(present) if ok]
            hy=np.zeros(len(chunks));hy[idx]=cosine_similarity(matrix,np.asarray(hyde_vector,dtype="float32").reshape(1,-1)).reshape(-1)
            signals.append(hy)
        fused=np.zeros(len(chunks))
        for s in signals:
            if np.max(s) > np.min(s):
                fused += 1.0 / (RRF_K + _ranks(s))
        result=[]
        for i in _rerank_order(query,chunks,fused):
            if len(result)>=limit:break
            c=chunks[int(i)];d=allowed[c["document_id"]]
            result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
              title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
              ai_allowed=d["ai_allowed"],score=round(float(fused[i]),4),kind="document"))
        return result
    chunks=fetch_all(conn,"SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version WHERE c.tenant_id=%s AND c.document_id=ANY(%s) ORDER BY c.document_id,c.ordinal LIMIT 20000",(p.tenant_id,list(allowed)))
    if not chunks:return []
    # ANN 相关性闸门（与主链路一致；无查询向量时不生效）
    if qv is not None:
        vecs=[_vec(c.get("embedding")) for c in chunks]
        chunks,vecs=_ann_gate(chunks,vecs,qv,load_config().ann_max_distance)
        if not chunks:return []
    tokens=[words(c["text"]) for c in chunks];terms=words(query);termset=set(terms)
    scores=np.maximum(np.asarray(BM25Okapi(tokens).get_scores(terms)),0)
    overlap=np.asarray([len(termset.intersection(ts))/max(1,len(termset)) for ts in tokens])
    try:
        model=TfidfVectorizer(analyzer="char",ngram_range=(2,4),max_features=24000)
        vectors=model.fit_transform([c["text"] for c in chunks])
        dense=cosine_similarity(model.transform([query]),vectors).reshape(-1)
    except ValueError:dense=np.zeros(len(chunks))
    signals=[scores,dense,overlap]
    if qv is not None:
        try:
            packed=_semantic_matrix(conn,chunks,qv.shape[1])
            if packed is not None:
                matrix,present=packed
                idx=[i for i,ok in enumerate(present) if ok]
                sem=np.zeros(len(chunks))
                sem[idx]=cosine_similarity(matrix,qv).reshape(-1)
                signals.append(sem)
                # HyDE 第四路：向量由调用方在脱敏与预算合规下生成，此处只做相似度
                if hyde_vector is not None:
                    hy=np.zeros(len(chunks))
                    hy[idx]=cosine_similarity(matrix,np.asarray(hyde_vector,dtype="float32").reshape(1,-1)).reshape(-1)
                    signals.append(hy)
        except Exception:pass
    fused=np.zeros(len(chunks))
    for s in signals:
        if np.max(s) > np.min(s):
            fused += 1.0 / (RRF_K + _ranks(s))
    result=[]
    for i in _rerank_order(query,chunks,fused):
        if len(result)>=limit:break
        c=chunks[int(i)];d=allowed[c["document_id"]]
        result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
          title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
          ai_allowed=d["ai_allowed"],score=round(float(fused[i]),4),kind="document"))
    return result
