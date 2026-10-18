#!/usr/bin/env python
"""全量重算 chunks.embedding：嵌入输入从纯正文改为"文档标题 + 正文"后的一次性回填。

口径与入库（worker._embed_chunks）、检索侧惰性补算（retriever._semantic_matrix）
一致，三者共用 embedding.embedding_input。标题只进向量：chunks.text 列不动，
ES 不动（BM25 走 ik 分词，与向量是互补信号）。

用法（项目根目录，生产口径）：
  PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python scripts/backfill_embeddings.py --dry-run
  PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python scripts/backfill_embeddings.py

- 嵌入服务不可用时直接失败退出（不留半成品向量），修复后重跑即可；
- 单事务写入：中途失败整体回滚，可安全重跑；只更新 embedding 列；
- 含历史版本切片（检索只认 current_version，多算无害，保持全库口径一致）。
"""
import argparse
import sys

import numpy as np

from ecom_copilot.enterprise.config import load_config
from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.embedding import available as embed_available
from ecom_copilot.enterprise.embedding import embedding_input, encode_passages

QUERY = """
SELECT c.id, c.text, d.title
FROM chunks c JOIN documents d ON d.id = c.document_id
ORDER BY c.id
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="按'文档标题+正文'口径全量重算 chunks.embedding")
    ap.add_argument("--batch", type=int, default=64, help="每批送嵌入服务的切片数（服务端内部再按 32 条分批）")
    ap.add_argument("--dry-run", action="store_true", help="只统计规模，不写库")
    args = ap.parse_args()

    cfg = load_config()
    if not embed_available():
        print(f"嵌入服务不可用（{cfg.embed_service_url} 探测失败），终止；不写半成品向量", file=sys.stderr)
        return 2

    with database_connection() as conn:
        rows = fetch_all(conn, QUERY)
    total = len(rows)
    print(f"待重算切片 {total} 条（含历史版本；检索只认 current_version，多算无害）")
    if args.dry_run or total == 0:
        return 0

    done = 0
    with database_connection() as conn:  # 单事务：中途失败整体回滚，重跑即可
        for start in range(0, total, args.batch):
            batch = rows[start:start + args.batch]
            vecs = encode_passages([embedding_input(r["title"], r["text"]) for r in batch])
            if len(vecs) != len(batch):
                print(f"批次 {start}-{start + len(batch)} 向量数不匹配（{len(vecs)}/{len(batch)}），终止", file=sys.stderr)
                return 3
            if vecs.shape[1] != 512:
                print(f"嵌入维度 {vecs.shape[1]} 与 PG 列 vector(512) 不一致，终止", file=sys.stderr)
                return 3
            for r, v in zip(batch, vecs):
                conn.execute("UPDATE chunks SET embedding=%s WHERE id=%s", (np.asarray(v, dtype="float32"), r["id"]))
            done += len(batch)
            print(f"已重算 {done}/{total}", flush=True)
    print(f"完成：{done} 条切片已按'标题+正文'口径重算")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
