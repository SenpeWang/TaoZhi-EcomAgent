"""授权候选内的不可变检索：BM25 + 本地字符向量 + 语义向量，禁止先全局检索再隐藏。

三路信号各自排序后用倒数排名融合（RRF，k=60），任何一路失效只收窄召回面：
嵌入服务不可用或模型缺失时自动降级为两路词法检索，不阻断问答。
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

def search_documents(conn,p,query,limit=8,hyde_vector=None):
    docs=fetch_all(conn,"SELECT * FROM documents WHERE tenant_id=%s AND state='published'",(p.tenant_id,))
    allowed={d["id"]:d for d in docs if can_read(conn,p,d)}
    if not allowed:return []
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
    for s in signals:fused+=1.0/(RRF_K+_ranks(s))
    result=[]
    for i in np.argsort(-fused):
        if len(result)>=limit or fused[i]<RRF_FLOOR:break
        c=chunks[int(i)];d=allowed[c["document_id"]]
        result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
          title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
          ai_allowed=d["ai_allowed"],score=round(float(fused[i]),4),kind="document"))
    return result
