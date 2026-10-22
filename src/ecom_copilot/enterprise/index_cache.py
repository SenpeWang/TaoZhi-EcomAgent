"""语料切片向量的索引缓存：指纹命中就复用向量，只有未命中的才调嵌入模型。

为什么缓存在最前一层
--------------------
RAG 的成本大头是"把语料变成向量"。文档重新解析、重复灌库、跨版本重灌时，
绝大多数切片的正文并没有变——指纹相同的文本不该再烧一次嵌入算力。
缓存之后才轮到"怎么检索上下文"：融合、精排、密级过滤不受本层影响。

键怎么构成
----------
PG 主键 (tenant_id, model_tag, digest)：
- digest = sha256(embedding_input(标题, 正文))，嵌入输入与入库侧同一个函数，
  口径一变指纹就变，不会把老口径的向量当缓存用；
- model_tag = 嵌入服务 /health 报的 "model:dim"。换模型或换维度后，
  所有历史键自动命中不上，天然失效，不需要额外的清理动作；
- tenant_id 参与键：只在同一租户内复用，杜绝"这段文本在不在对方库里"
  这种跨租户存在性探测。

隔离级别
--------
读写都包在 SAVEPOINT 里。缓存是加速器，不是依赖：表缺失或写失败都只丢
缓存，不能让一次入库事务被它带崩——这在 PG 里是硬性的，语句报错不回滚
整段就会让后续命令全部失效。

容量：键随"去重后的切片正文数 × 模型版本"增长，与语料规模同阶，不随查询量膨胀。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from .db import fetch_all
from .embedding import digest, embedding_input, encode_passages, model_signature, to_vector


def _lookup(conn, tenant_id: str, tag: str, digests: Sequence[str],
            dim: int) -> dict:
    """按指纹批量取回已有向量；读失败按未命中处理，维度不符的一律丢弃。"""
    if not digests:
        return {}
    try:
        with conn.transaction():   # 外层已在事务中 → 这里是 SAVEPOINT
            rows = fetch_all(conn, "SELECT digest, embedding FROM embedding_cache "
                                   "WHERE tenant_id=%s AND model_tag=%s AND digest=ANY(%s)",
                             (tenant_id, tag, list(digests)))
    except Exception as exc:
        print("嵌入缓存读取失败（按未命中处理）", type(exc).__name__, flush=True)
        return {}
    found = {}
    for row in rows:
        v = to_vector(row["embedding"])
        if v is not None and len(v) == dim:
            found[row["digest"]] = v
    return found


def _store(conn, tenant_id: str, tag: str, dim: int, items) -> None:
    """写回本次新算出的向量；写失败只丢缓存，不影响已算出的结果。"""
    try:
        with conn.transaction():   # 同上：失败只回滚这一段
            for d, v in items:
                conn.execute(
                    "INSERT INTO embedding_cache(tenant_id,model_tag,digest,dim,embedding) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,model_tag,digest) "
                    "DO UPDATE SET embedding=EXCLUDED.embedding, last_used_at=now()",
                    (tenant_id, tag, d, dim, v))
    except Exception as exc:
        print("嵌入缓存写入失败（不影响本次结果）", type(exc).__name__, flush=True)


def encode_index(conn, tenant_id: str,
                 pairs: Sequence[Tuple[str, str]]) -> Optional[np.ndarray]:
    """语料切片编码（带索引缓存），返回与 pairs 对齐的 (n, dim) float32 矩阵。

    pairs：[(文档标题, 切片正文), ...]。命中缓存的切片不消耗嵌入算力，
    只把未命中的那部分送去编码，算完立即写回缓存。

    嵌入服务不可用时返回 None：调用方留空向量，交给检索侧惰性补算。
    """
    if not pairs:
        return None
    tag = model_signature()
    if not tag:
        return None
    dim = int(tag.rsplit(":", 1)[1])
    inputs = [embedding_input(title, text) for title, text in pairs]
    digests = [digest(x) for x in inputs]

    found = _lookup(conn, tenant_id, tag, digests, dim)
    missing = [i for i, d in enumerate(digests) if d not in found]
    if missing:
        vecs = encode_passages([inputs[i] for i in missing])
        if len(vecs) != len(missing):
            return None
        computed = []
        for j, i in enumerate(missing):
            vec = np.asarray(vecs[j], dtype="float32")
            found[digests[i]] = vec
            computed.append((digests[i], vec))
        _store(conn, tenant_id, tag, dim, computed)

    return np.stack([found[d] for d in digests]).astype("float32", copy=False)
