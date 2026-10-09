"""Elasticsearch 检索索引：切片双路召回（BM25 + kNN），权限过滤前置。

约定：
- 索引文档 id = chunk_id，与数据库 chunks 一一对应，重解析按 document_id+version 先删后写；
- 检索时把授权文档清单编译进 ES 过滤条件，未授权切片不进入召回面；
- ES 不可达时检索侧自动回落本地五信号链路，不阻断问答。
"""
from __future__ import annotations
from .config import load_config

FALLBACK_ANALYZER="cjk"

def _analyzers(es) -> tuple:
    """返回 (索引分析器, 查询分析器)；IK 插件缺失时确保回落内置 cjk。

    索引映射一经建立便不可更改，因此建索引前用探针请求确认分析器可用，
    避免在没有 analysis-ik 的集群上写入失败。
    """
    cfg=load_config()
    if cfg.es_analyzer==FALLBACK_ANALYZER:
        return cfg.es_analyzer,cfg.es_search_analyzer
    try:
        es.indices.analyze(analyzer=cfg.es_analyzer,text="分词探针")
        es.indices.analyze(analyzer=cfg.es_search_analyzer,text="分词探针")
        return cfg.es_analyzer,cfg.es_search_analyzer
    except Exception as exc:
        print("IK 分词器不可用，回落内置 cjk:",type(exc).__name__,flush=True)
        return FALLBACK_ANALYZER,FALLBACK_ANALYZER

def index_mapping(es=None) -> dict:
    """构造索引映射；传入 ES 客户端时按其实际插件能力选择分析器。"""
    if es is None:
        es,_=_client()
    analyzer,search_analyzer=_analyzers(es)
    return {
        "settings":{"number_of_shards":1,"number_of_replicas":0},
        "mappings":{"properties":{
            "tenant_id":{"type":"keyword"},
            "document_id":{"type":"keyword"},
            "version":{"type":"integer"},
            "ordinal":{"type":"integer"},
            "page":{"type":"integer"},
            "text":{"type":"text","analyzer":analyzer,"search_analyzer":search_analyzer},
            "embedding":{"type":"dense_vector","dims":512,"index":True,"similarity":"cosine"},
        }},
    }

def _client():
    from elasticsearch import Elasticsearch
    cfg=load_config()
    es=Elasticsearch(cfg.es_url,request_timeout=10)
    if not es.ping():raise RuntimeError("ES 无法连接")
    return es,cfg.es_index

def available() -> bool:
    if not load_config().es_enabled:return False
    try:
        _client();return True
    except Exception:return False

def ensure_index() -> None:
    es,index=_client()
    if not es.indices.exists(index=index):
        es.indices.create(index=index,**index_mapping(es))
        # 建索引后分片尚未分配完成时写入会报 NoShardAvailableActionException。
        es.cluster.health(index=index,wait_for_status="yellow",timeout="30s")

def index_chunks(rows) -> int:
    """rows: 数据库 chunks 行（含 id/tenant_id/document_id/version/ordinal/page/text/embedding）。"""
    if not rows:return 0
    from elasticsearch.helpers import bulk
    es,index=_client();ensure_index()
    def gen():
        for r in rows:
            emb=r.get("embedding")
            doc={"tenant_id":r["tenant_id"],"document_id":r["document_id"],"version":r["version"],
                 "ordinal":r["ordinal"],"page":r["page"],"text":r["text"],
                 "embedding":(None if not emb or len(emb)!=512*4 else __import__("numpy").frombuffer(emb,dtype="float32").tolist())}
            yield {"_index":index,"_id":r["id"],"_source":doc}
    ok,_=bulk(es,gen(),raise_on_error=False,refresh=False)
    return ok

def delete_chunks(document_id: str, version=None) -> None:
    es,index=_client()
    if version is None:
        es.delete_by_query(index=index,query={"term":{"document_id":document_id}},refresh=True)
    else:
        es.delete_by_query(index=index,query={"bool":{"filter":[
            {"term":{"document_id":document_id}},{"term":{"version":version}}]}},refresh=True)

def search_candidates(tenant_id: str, allowed_doc_ids: list, query_vector=None, size: int=200, query: str = ""):
    """返回 (bm25 命中 {chunk_id: rank}, knn 命中 {chunk_id: rank})，rank 从 1 起。"""
    es,index=_client()
    acl={"bool":{"filter":[
        {"term":{"tenant_id":tenant_id}},
        {"terms":{"document_id":list(allowed_doc_ids)}}]}}
    bm25={}
    bm25_query={"bool":{"must":[{"match":{"text":query}}],"filter":acl["bool"]["filter"]}} if query else acl
    r=es.search(index=index,size=size,query=bm25_query,_source=False)
    for rank,hit in enumerate(r["hits"]["hits"],1):bm25[hit["_id"]]=rank
    knn={}
    if query_vector is not None:
        r=es.search(index=index,knn={"field":"embedding","query_vector":query_vector.reshape(-1).tolist(),
            "k":min(size,1000),"num_candidates":max(size*2,500),"filter":acl["bool"]["filter"]},_source=False)
        for rank,hit in enumerate(r["hits"]["hits"],1):knn[hit["_id"]]=rank
    return bm25,knn
