"""授权候选内的不可变检索：BM25 + 本地字符向量 + 语义向量，禁止先全局检索再隐藏。

三路信号各自排序后用倒数排名融合（RRF，k=60），任何一路失效只收窄召回面：
- ES 可用：BM25 与语义 kNN 两路由 Elasticsearch 承担（权限过滤前置），本地补算其余信号；
- ES 不可达：全部信号本地计算（原链路），自动降级不阻断问答。
语义向量在入库时由 Worker 计算（chunks.embedding），存量切片在检索时惰性补算。
"""
import re,numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rank_bm25 import BM25Okapi
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

def _semantic_matrix(conn,chunks,dim):
    """返回与 chunks 对齐的向量矩阵及存在标记；缺失向量惰性补算（限流），失败返回 None。"""
    try:
        from .embedding import available as embed_available,encode_passages
        if not embed_available():return None
        mats=[None]*len(chunks);missing=[]
        for i,c in enumerate(chunks):
            raw=c.get("embedding")
            if raw and len(raw)==dim*4:mats[i]=np.frombuffer(raw,dtype="float32")
            else:missing.append(i)
        if missing:
            picked=missing[:BACKFILL_LIMIT]
            vecs=encode_passages([chunks[i]["text"] for i in picked])
            for i,v in zip(picked,vecs):
                mats[i]=v
                try:
                    # 独立连接写回：补算失败只损失增量向量，不污染调用方检索事务
                    from .db import database_connection
                    with database_connection() as bc:bc.execute("UPDATE chunks SET embedding=%s WHERE id=%s",(v.tobytes(),chunks[i]["id"]))
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

def search_documents(conn,p,query,limit=8,hyde_vector=None):
    docs=fetch_all(conn,"SELECT * FROM documents WHERE tenant_id=%s AND state='published'",(p.tenant_id,))
    allowed={d["id"]:d for d in docs if can_read(conn,p,d)}
    if not allowed:return []
    qv=None
    es_ranks=None
    try:
        from .embedding import available as embed_available,encode_query
        if embed_available():qv=encode_query(query)
    except Exception:qv=None
    # ES 承担 BM25 + kNN 双路召回，权限过滤前置；失败回落本地全量链路
    try:
        from . import es_index
        if es_index.available():
            es_ranks=es_index.search_candidates(p.tenant_id,list(allowed),qv,query=query)
    except Exception:es_ranks=None
    if es_ranks is not None:
        bm25_rank,knn_rank=es_ranks
        ids=list(dict.fromkeys(list(bm25_rank)+list(knn_rank)))
        chunks=[c for c in fetch_all(conn,"SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version WHERE c.tenant_id=%s AND c.document_id=ANY(%s) AND c.id=ANY(%s) ORDER BY c.document_id,c.ordinal",(p.tenant_id,list(allowed),ids)) if c["id"] in set(ids)]
        if not chunks:return []
        signals=[_es_rank_signal(bm25_rank,chunks),_es_rank_signal(knn_rank,chunks)]
        tokens=[words(c["text"]) for c in chunks];terms=words(query);termset=set(terms)
        overlap=np.asarray([len(termset.intersection(ts))/max(1,len(termset)) for ts in tokens])
        signals.append(overlap)
        try:
            model=TfidfVectorizer(analyzer="char",ngram_range=(2,4),max_features=24000)
            vectors=model.fit_transform([c["text"] for c in chunks])
            dense=cosine_similarity(model.transform([query]),vectors).reshape(-1)
        except ValueError:dense=np.zeros(len(chunks))
        signals.append(dense)
        present=[c.get("embedding") and len(c["embedding"])==SEMANTIC_DIM*4 for c in chunks]
        if any(present) and qv is not None and hyde_vector is not None:
            # 语义相似度已由 kNN 路承担，这里只补 HyDE 第四路
            matrix=np.stack([np.frombuffer(c["embedding"],dtype="float32") for c,ok in zip(chunks,present) if ok])
            idx=[i for i,ok in enumerate(present) if ok]
            hy=np.zeros(len(chunks));hy[idx]=cosine_similarity(matrix,np.asarray(hyde_vector,dtype="float32").reshape(1,-1)).reshape(-1)
            signals.append(hy)
        fused=np.zeros(len(chunks))
        for s in signals:
            if np.max(s) > np.min(s):
                fused += 1.0 / (RRF_K + _ranks(s))
        result=[]
        for i in np.argsort(-fused):
            if len(result)>=limit or fused[i]<RRF_FLOOR:break
            c=chunks[int(i)];d=allowed[c["document_id"]]
            result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
              title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
              ai_allowed=d["ai_allowed"],score=round(float(fused[i]),4),kind="document"))
        return result
    chunks=fetch_all(conn,"SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version WHERE c.tenant_id=%s AND c.document_id=ANY(%s) ORDER BY c.document_id,c.ordinal LIMIT 20000",(p.tenant_id,list(allowed)))
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
    try:
        from .embedding import available as embed_available,encode_query
        if embed_available():
            qv=encode_query(query)
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
    for i in np.argsort(-fused):
        if len(result)>=limit or fused[i]<RRF_FLOOR:break
        c=chunks[int(i)];d=allowed[c["document_id"]]
        result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
          title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
          ai_allowed=d["ai_allowed"],score=round(float(fused[i]),4),kind="document"))
    return result
