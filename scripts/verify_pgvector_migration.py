"""迁移验证：ES 租户污染检查 + HNSW 近似检索 vs 暴力精确检索召回一致率。"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taozhi.config import load_config  # noqa: E402
from taozhi.knowledge.db import database_connection, fetch_all, fetch_one  # noqa: E402
from taozhi.knowledge.embedding import encode_query  # noqa: E402
from taozhi.knowledge.retriever import _pg_ann  # noqa: E402


def main() -> None:
    """按当前环境配置动态查询目标租户与 ES 索引开展对比验证。"""
    cfg = load_config()
    es_search_url = f"{cfg.es_url.rstrip('/')}/{cfg.es_index}/_search"
    body = json.dumps({"size": 0, "aggs": {"t": {"terms": {"field": "tenant_id"}}}}).encode()
    req = urllib.request.Request(es_search_url, data=body, headers={"Content-Type": "application/json"})
    aggs = json.loads(urllib.request.urlopen(req, timeout=10).read())["aggregations"]["t"]["buckets"]
    print("ES 租户分布:", [(b["key"], b["doc_count"]) for b in aggs])

    queries = [
        "膜切机规格书",
        "钢化膜 C1 高清",
        "售后换新政策",
        "MagSafe 壳 T20 适配机型",
        "客服考核题库",
        "水凝膜背膜规格",
        "采购供应商底价",
        "店铺运营作业指导",
        "陶瓷膜 C5 折叠屏",
        "防窥膜 C3",
    ]
    with database_connection() as c:
        tenant_row = fetch_one(c, "SELECT id FROM tenants WHERE code=%s", (cfg.tenant_code,))
        if not tenant_row:
            raise RuntimeError(f"未找到租户配置 code={cfg.tenant_code}")
        tenant_id = tenant_row["id"]
        allrows = fetch_all(
            c,
            """SELECT c.id, c.embedding FROM chunks c
            JOIN documents d ON d.id=c.document_id AND d.current_version=c.version
            WHERE d.tenant_id=%s AND d.state='published' AND c.embedding IS NOT NULL""",
            (tenant_id,),
        )
        allowed = fetch_all(c, "SELECT id FROM documents WHERE tenant_id=%s AND state='published'", (tenant_id,))
        ids = [r["id"] for r in allrows]
        mat = np.stack([r["embedding"].to_numpy() for r in allrows])
        print("参与对比的切片数:", len(ids))
        total_hit = 0
        for q in queries:
            qv = encode_query(q)[0]
            approx = _pg_ann(c, tenant_id, [r["id"] for r in allowed], qv, 10)
            d = mat @ qv
            exact = [ids[i] for i in np.argsort(-d)[:10]]
            hit = len(set(approx) & set(exact))
            total_hit += hit
            print(f"  {q}: HNSW top10 与精确 top10 重合 {hit}/10")
    print(f"总体召回一致率: {total_hit}/{10 * len(queries)} = {total_hit / (10 * len(queries)):.0%}")


if __name__ == "__main__":
    main()
