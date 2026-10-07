"""授权候选内的不可变 BM25/本地向量检索，禁止先全局检索再隐藏。"""
import re,numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rank_bm25 import BM25Okapi
from .db import fetch_all
from .policy import can_read
from .privacy import redact

def words(text):
    import jieba
    return [x.casefold() for x in jieba.lcut(text) if x.strip() and (len(x)>1 or x.isalnum())]
def search_documents(conn,p,query,limit=8):
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
    scores=(scores/(scores.max()+1e-9))*0.4+dense*0.4+overlap*0.2
    result=[]
    for i in np.argsort(-scores):
        if len(result)>=limit or scores[i]<0.015:break
        c=chunks[int(i)];d=allowed[c["document_id"]]
        result.append(dict(document_id=d["id"],version=c["version"],acl_version=d["acl_version"],chunk_id=c["id"],
          title=d["title"],quote=c["text"] if p.boss else redact(c["text"]),page=c["page"],level=d["level"],node_id=d["node_id"],tenant_id=p.tenant_id,
          ai_allowed=d["ai_allowed"],score=round(float(scores[i]),4),kind="document"))
    return result
