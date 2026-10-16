"""对照实验：随机数据（无聚类）vs 聚类数据（模拟真实嵌入）的 HNSW 召回曲线。

目的：区分「ANN 在均匀随机数据上的固有退化」与「索引缺陷」。
生产库真实 bge 向量已测出 ef_search=40 下 recall@10=100%（见 verify_pgvector_migration.py）。
"""
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np

from ecom_copilot.enterprise.db import database_connection, fetch_all

N = 100_000
DIM = 512
rng = np.random.default_rng(7)


def fmt(v):
    return "[" + ",".join(repr(float(x)) for x in v.tolist()) + "]"


def recall_at(c, table, vecs, qs, ef, k=10):
    hit = 0
    for q in qs:
        approx = {r["id"] for r in fetch_all(c, f"select id from {table} order by emb <=> %s::vector limit {k}", (fmt(q),))}
        d = vecs @ q
        exact = {j + 1 for j in np.argsort(-d)[:k]}
        hit += len(approx & exact)
    return hit / (k * len(qs))


with database_connection() as c:
    # ── 数据 A：均匀随机（无聚类，ANN 最坏情形） ──
    vecs_rand = rng.standard_normal((N, DIM)).astype("float32")
    vecs_rand /= np.linalg.norm(vecs_rand, axis=1, keepdims=True)
    # ── 数据 B：1000 个簇心 + 小噪声（模拟真实嵌入的聚类结构） ──
    centers = rng.standard_normal((1000, DIM)).astype("float32")
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    idx = rng.integers(0, 1000, N)
    vecs_clu = (centers[idx] + 0.08 * rng.standard_normal((N, DIM)).astype("float32"))
    vecs_clu /= np.linalg.norm(vecs_clu, axis=1, keepdims=True)

    qs = rng.standard_normal((30, DIM)).astype("float32")
    qs /= np.linalg.norm(qs, axis=1, keepdims=True)

    for name, vecs in (("随机数据", vecs_rand), ("聚类数据", vecs_clu)):
        c.execute(f"drop table if exists bench_{name}")
        c.execute(f"create table bench_{name}(id bigserial primary key, emb vector(512))")
        t0 = time.time()
        c.cursor().executemany(f"insert into bench_{name}(emb) values(%s)", [(fmt(v),) for v in vecs])
        c.execute("set maintenance_work_mem='2GB'")
        c.execute("set max_parallel_maintenance_workers=8")
        c.execute(f"create index on bench_{name} using hnsw(emb vector_cosine_ops) with (m=16, ef_construction=100)")
        print(f"[{name}] 插入+建索引 {time.time() - t0:.0f}s", flush=True)
        for ef in (40, 100, 400, 1000):
            c.execute(f"set hnsw.ef_search={ef}")
            print(f"[{name}] ef_search={ef}: recall@10 = {recall_at(c, f'bench_{name}', vecs, qs, ef):.0%}", flush=True)
        c.execute(f"drop table bench_{name}")
    print("实验表已清理")
