"""规模基准：10 万条 512 维向量的 HNSW 构建耗时、查询延迟、召回一致率与内存占用。

用于验证迁移到 PG pgvector 后的容量路径（百万级无压力；千万级需 halfvec/降维/加内存）。
基准表 chunks_bench 与生产数据隔离，可随时 drop。
"""
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np

from ecom_copilot.enterprise.db import database_connection, fetch_all, fetch_one

N = 100_000
DIM = 512
rng = np.random.default_rng(42)


def fmt(v):
    return "[" + ",".join(repr(float(x)) for x in v.tolist()) + "]"


with database_connection() as c:
    pid = fetch_one(c, "select pg_backend_pid() as p")["p"]

    def rss_mb():
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) / 1024
        return -1

    c.execute("drop table if exists chunks_bench")
    c.execute("create table chunks_bench(id bigserial primary key, grp int, emb vector(512))")
    vecs = rng.standard_normal((N, DIM)).astype("float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    t0 = time.time()
    data = [(i % 100, fmt(v)) for i, v in enumerate(vecs)]
    c.cursor().executemany("insert into chunks_bench(grp,emb) values(%s,%s)", data)
    print(f"插入 {N} 条: {time.time() - t0:.1f}s, RSS {rss_mb():.0f} MB", flush=True)

    t0 = time.time()
    # 批量建索引必须调大维护内存与并行度：默认 64MB/2 worker 在 12 核机器上 10 万条要 15 分钟以上
    c.execute("set maintenance_work_mem='2GB'")
    c.execute("set max_parallel_maintenance_workers=8")
    c.execute("create index chunks_bench_hnsw on chunks_bench using hnsw(emb vector_cosine_ops) with (m=16, ef_construction=100)")
    print(f"HNSW 构建(2GB/8 worker): {time.time() - t0:.1f}s, RSS {rss_mb():.0f} MB", flush=True)

    qs = rng.standard_normal((20, DIM)).astype("float32")
    qs /= np.linalg.norm(qs, axis=1, keepdims=True)

    # 默认 ef_search=40 的查询延迟
    lat = []
    for q in qs:
        t0 = time.time()
        fetch_all(c, "select id from chunks_bench order by emb <=> %s::vector limit 10", (fmt(q),))
        lat.append(time.time() - t0)
    lat = np.array(lat[2:])  # 去掉前两次预热
    print(f"无过滤查询延迟(ef_search=40): 平均 {lat.mean() * 1000:.1f}ms, P95 {np.percentile(lat, 95) * 1000:.1f}ms")

    # ef_search=100 的延迟与召回
    c.execute("set hnsw.ef_search=100")
    lat2 = []
    for q in qs:
        t0 = time.time()
        fetch_all(c, "select id from chunks_bench order by emb <=> %s::vector limit 10", (fmt(q),))
        lat2.append(time.time() - t0)
    lat2 = np.array(lat2[2:])
    print(f"无过滤查询延迟(ef_search=100): 平均 {lat2.mean() * 1000:.1f}ms, P95 {np.percentile(lat2, 95) * 1000:.1f}ms")

    # 召回一致率：HNSW(ef_search=100) vs 内存暴力精确
    hit = 0
    for i, q in enumerate(qs):
        approx = {r["id"] for r in fetch_all(c, "select id from chunks_bench order by emb <=> %s::vector limit 10", (fmt(q),))}
        d = vecs @ q
        exact = {j + 1 for j in np.argsort(-d)[:10]}
        hit += len(approx & exact)
    print(f"召回一致率(ef_search=100, top10): {hit}/{10 * len(qs)} = {hit / (10 * len(qs)):.0%}")

    # 带权限过滤（grp=单组，模拟 document_id=ANY 过滤）的延迟与召回
    lat3 = []
    hit3 = 0
    for i, q in enumerate(qs):
        t0 = time.time()
        approx = {r["id"] for r in fetch_all(c, "select id from chunks_bench where grp=%s order by emb <=> %s::vector limit 10", (i % 100, fmt(q)))}
        lat3.append(time.time() - t0)
        d = vecs @ q
        mask = np.arange(N) % 100 == (i % 100)
        idx = np.where(mask)[0]
        top = idx[np.argsort(-d[idx])[:10]]
        exact = {j + 1 for j in top}
        hit3 += len(approx & exact)
    lat3 = np.array(lat3[2:])
    print(f"分组过滤查询延迟: 平均 {lat3.mean() * 1000:.1f}ms")
    print(f"分组过滤召回一致率(top10): {hit3}/{10 * len(qs)} = {hit3 / (10 * len(qs)):.0%}")

    print(f"结束后 RSS {rss_mb():.0f} MB", flush=True)
    c.execute("drop table chunks_bench")
    print("基准表已清理")
