"""迁移验证：ES 租户污染检查 + HNSW 近似检索 vs 暴力精确检索召回一致率。"""
import sys
sys.path.insert(0, "/home/sp/TaoZhi-EcomAgent/src")
import json
import urllib.request

import numpy as np

from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.embedding import encode_query
from ecom_copilot.enterprise.retriever import _pg_ann

# 1) ES 污染检查：只应存在生产租户
body = json.dumps({"size": 0, "aggs": {"t": {"terms": {"field": "tenant_id"}}}}).encode()
req = urllib.request.Request("http://127.0.0.1:9200/chunks/_search", data=body,
                             headers={"Content-Type": "application/json"})
aggs = json.loads(urllib.request.urlopen(req, timeout=10).read())["aggregations"]["t"]["buckets"]
print("ES 租户分布:", [(b["key"], b["doc_count"]) for b in aggs])

# 2) HNSW 近似 vs 暴力精确：10 个真实 query 的 top10 重合度
queries = ["膜切机规格书", "钢化膜 C1 高清", "售后换新政策", "MagSafe 壳 T20 适配机型",
           "客服考核题库", "水凝膜背膜规格", "采购供应商底价", "店铺运营作业指导",
           "陶瓷膜 C5 折叠屏", "防窥膜 C3"]
with database_connection() as c:
    allrows = fetch_all(c, """select c.id, c.embedding from chunks c
        join documents d on d.id=c.document_id and d.current_version=c.version
        where d.state='published' and c.embedding is not null""")
    allowed = fetch_all(c, "select id from documents where state='published'")
    ids = [r["id"] for r in allrows]
    mat = np.stack([r["embedding"].to_numpy() for r in allrows])
    print("参与对比的切片数:", len(ids))
    total_hit = 0
    for q in queries:
        qv = encode_query(q)[0]
        approx = _pg_ann(c, "tenant_a6c05c410da14e3dc2", [r["id"] for r in allowed], qv, 10)
        d = mat @ qv
        exact = [ids[i] for i in np.argsort(-d)[:10]]
        hit = len(set(approx) & set(exact))
        total_hit += hit
        print(f"  {q}: HNSW top10 与精确 top10 重合 {hit}/10")
print(f"总体召回一致率: {total_hit}/{10 * len(queries)} = {total_hit / (10 * len(queries)):.0%}")
