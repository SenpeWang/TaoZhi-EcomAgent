"""真实几何对照：query 与文档同分布（从簇心采样），模拟真实语义检索的 HNSW 召回。

前序实验（experiment_hnsw_recall.py）用随机 query 打聚类文档，query 离所有文档都远，
属于对抗几何；本实验让 query 与文档同分布，对应生产中"查询贴近相关文档"的真实情形。
"""
import sys
import time

sys.path.insert(0, "/home/sp/TaoZhi-EcomAgent/src")

import numpy as np

from ecom_copilot.enterprise.db import database_connection, fetch_all

N = 100_000
DIM = 512
rng = np.random.default_rng(11)


def fmt(v):
    return "[" + ",".join(repr(float(x)) for x in v.tolist()) + "]"


centers = rng.standard_normal((1000, DIM)).astype("float32")
centers /= np.linalg.norm(centers, axis=1, keepdims=True)
idx = rng.integers(0, 1000, N)
vecs = centers[idx] + 0.08 * rng.standard_normal((N, DIM)).astype("float32")
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

# query 与文档同分布：簇心 + 同级噪声（真实语义查询的几何形态）
qidx = rng.integers(0, 1000, 30)
qs = centers[qidx] + 0.08 * rng.standard_normal((30, DIM)).astype("float32")
qs /= np.linalg.norm(qs, axis=1, keepdims=True)

with database_connection() as c:
    c.execute("drop table if exists bench_real")
    c.execute("create table bench_real(id bigserial primary key, emb vector(512))")
    t0 = time.time()
    c.cursor().executemany("insert into bench_real(emb) values(%s)", [(fmt(v),) for v in vecs])
    c.execute("set maintenance_work_mem='2GB'")
    c.execute("set max_parallel_maintenance_workers=8")
    c.execute("create index on bench_real using hnsw(emb vector_cosine_ops) with (m=16, ef_construction=100)")
    print(f"插入+建索引 {time.time() - t0:.0f}s", flush=True)
    for ef in (40, 100):
        c.execute(f"set hnsw.ef_search={ef}")
        hit = 0
        for q in qs:
            approx = {r["id"] for r in fetch_all(c, "select id from bench_real order by emb <=> %s::vector limit 10", (fmt(q),))}
            d = vecs @ q
            exact = {j + 1 for j in np.argsort(-d)[:10]}
            hit += len(approx & exact)
        print(f"同分布 query, ef_search={ef}: recall@10 = {hit / 300:.0%}", flush=True)
    c.execute("drop table bench_real")
    print("实验表已清理")
