"""存量切片重建 Elasticsearch 索引。

用法：
    ECOM_ENV_FILE=.env.demo .venv/bin/python scripts/rebuild_es_index.py [--drop]

行为：
- 确认 ES 可用且 es_enabled；--drop 时先删除整个索引再建；
- 分批把当前版本全部切片（含向量）写入 chunks_v1。
"""
from __future__ import annotations
import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise import es_index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="重建前删除整个索引")
    args = parser.parse_args()

    from ecom_copilot.enterprise.config import load_config
    if not load_config().es_enabled:
        print("ES 未启用：请设置 ECOM_ES_ENABLED=\"true\" 后重试")
        return 1
    if not es_index.available():
        print("ES 无法连接，终止重建")
        return 1
    if args.drop:
        es, index = es_index._client()
        if es.indices.exists(index=index):
            es.indices.delete(index=index)
            print(f"已删除索引 {index}")
    es_index.ensure_index()
    total, batch = 0, 2000
    with database_connection() as c:
        cur = c.cursor()
        cur.execute("""SELECT c.id,c.tenant_id,c.document_id,c.version,c.ordinal,c.page,c.text,c.embedding
            FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_version=c.version
            ORDER BY c.document_id,c.ordinal""")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            total += es_index.index_chunks(rows)
            print(f"已索引 {total}")
    print(f"完成: 共索引 {total} 个切片")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
