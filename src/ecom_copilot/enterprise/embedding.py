"""本地语义向量服务客户端：HTTP 取向量，服务不可用时自动降级为词法检索。

模型与重依赖只存在于独立嵌入服务进程（llm 环境，scripts/embedding_server.py），
本模块不引入 torch/transformers。文档内容只发往 127.0.0.1 回环地址。

本模块同时是"向量怎么编码、怎么解码、怎么取指纹"的唯一实现处：
- embedding_input：语料切片的嵌入输入口径（标题前缀）
- digest：文本指纹，缓存键的一段
- to_vector：库里读回的向量还原
- encode_index（见 index_cache.py）与 encode_query 都以此为基准
"""
import hashlib
import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

import httpx
import numpy as np

from .config import load_config

_lock = threading.Lock()
_state = {"checked_at": 0.0, "ok": False, "model": "", "dim": 0}
OK_TTL = 10.0
FAIL_TTL = 10.0

# 查询向量缓存：进程内 LRU。查询文本无界，刻意不落库——落库会让缓存表随用户
# 提问无限膨胀，而它省下的只是一次 ~19ms 的网络往返。
QUERY_CACHE_MAX = 2048
_query_cache: OrderedDict = OrderedDict()
_query_stats = {"hits": 0, "misses": 0}


def _probe() -> Tuple[bool, str, int]:
    """探测嵌入服务，返回 (可用, 模型名, 维度)，结果 TTL 缓存。

    一并取回模型标识：索引缓存必须知道"这批向量是哪个模型算的"，
    否则换模型后会误用旧向量，而这种错误不报错、只会让检索悄悄变差。
    """
    now = time.monotonic()
    with _lock:
        if now < _state["checked_at"]:
            return _state["ok"], _state["model"], _state["dim"]
    ok, model, dim = False, "", 0
    try:
        r = httpx.get(load_config().embed_service_url.rstrip("/") + "/health",
                  timeout=1.5, trust_env=False)
        data = r.json() if r.status_code == 200 else {}
        ok = r.status_code == 200 and data.get("status") == "ok"
        model = str(data.get("model") or "")
        dim = int(data.get("dim") or 0)
    except Exception:
        ok, model, dim = False, "", 0
    with _lock:
        _state["ok"] = ok
        _state["model"] = model
        _state["dim"] = dim
        _state["checked_at"] = now + (OK_TTL if ok else FAIL_TTL)
    return ok, model, dim


def available() -> bool:
    """嵌入服务是否可用（探测结果短时间缓存，避免每条查询都探测）。"""
    return _probe()[0]


def model_signature() -> str:
    """嵌入模型标识 ``model:dim``；取不到返回空串，调用方据此跳过缓存。"""
    ok, model, dim = _probe()
    return f"{model}:{dim}" if ok and dim else ""


def digest(text: str) -> str:
    """文本指纹（sha256）：缓存键的最后一段，决定这段文本要不要重新编码。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_vector(raw) -> Optional[np.ndarray]:
    """把库里读回的向量统一成 float32 数组；无法识别返回 None。

    读回形态取决于 pgvector-python 是否注册：注册后为 Vector（可转 numpy），
    未注册时为文本；历史 bytea 列也可能是字节串。放这里是为了让"向量怎么存、
    怎么读"只有一个实现。
    """
    if raw is None:
        return None
    if isinstance(raw, np.ndarray):
        return raw.astype("float32", copy=False)
    to_numpy = getattr(raw, "to_numpy", None)
    if callable(to_numpy):
        try:
            return to_numpy().astype("float32", copy=False)
        except Exception:
            return None
    if isinstance(raw, (bytes, bytearray, memoryview)) and len(raw) % 4 == 0:
        return np.frombuffer(raw, dtype="float32")
    return None


def _post(texts: list[str], instruct: bool) -> np.ndarray:
    r = httpx.post(load_config().embed_service_url.rstrip("/") + "/embed",
                   json={"texts": texts, "instruct": instruct}, timeout=60, trust_env=False)
    r.raise_for_status()
    return np.asarray(r.json()["vectors"], dtype="float32")


def embedding_input(title: str, text: str) -> str:
    """构造切片的嵌入输入：文档标题作为上下文前缀拼在正文前。

    Contextual retrieval：切片脱离文档后语义不完整（"支持 20W 快充"分不清是哪款
    产品），标题前缀让向量自带归属锚。标题只参与向量计算——chunks.text 列仍存纯
    正文（展示、精排、事实抽取不受影响），也不进 ES（BM25 与向量是互补信号）。
    入库、检索侧惰性补算、全量回填脚本三处必须共用本函数，保证向量口径一致。
    """
    title=(title or "").strip()
    return f"{title}\n{text}" if title else text


def encode_passages(texts: list[str]) -> np.ndarray:
    """对资料切片编码，返回 (n, dim) 的 L2 归一化 float32 矩阵。

    不缓存：调用方（入库/回填/惰性补算）统一走 index_cache.encode_index，
    那里按指纹复用；这里只负责"算"。
    """
    if not texts:
        return np.zeros((0, 1), dtype="float32")
    return _post(list(texts), instruct=False)


def encode_query(query: str) -> np.ndarray:
    """对查询编码（附带检索指令前缀），返回 (1, dim) 向量。

    先查进程内 LRU 缓存：同一 worker 里同样的提问只编码一次。缓存键含
    model_tag —— 嵌入服务换模型或维度后，老键自动失效。进程重启即清空，
    这不算损失：它省的是一次网络往返，不是离线重算。
    """
    tag = model_signature()
    key = f"{tag}|{digest(query)}"
    with _lock:
        hit = _query_cache.get(key)
        if hit is not None:
            _query_cache.move_to_end(key)
            _query_stats["hits"] += 1
            return hit
        _query_stats["misses"] += 1
    vec = _post([query], instruct=True)
    with _lock:
        _query_cache[key] = vec
        _query_cache.move_to_end(key)
        while len(_query_cache) > QUERY_CACHE_MAX:
            _query_cache.popitem(last=False)
    return vec


def rerank(query: str, texts: list[str]) -> list[float]:
    """交叉编码器精排打分：返回与 texts 输入顺序对齐的相关性概率（0-1）。

    服务不可用或精排模型缺失（503）时抛异常，由调用方回落融合序。
    """
    r = httpx.post(load_config().embed_service_url.rstrip("/") + "/rerank",
                   json={"query": query, "texts": list(texts)},
                   timeout=load_config().rerank_timeout, trust_env=False)
    r.raise_for_status()
    return [float(s) for s in r.json()["scores"]]


def query_cache_stats() -> dict:
    """查询向量缓存的命中情况，供观测与回归测试断言。"""
    with _lock:
        hits, misses = _query_stats["hits"], _query_stats["misses"]
        size = len(_query_cache)
    total = hits + misses
    return {"hits": hits, "misses": misses, "size": size,
            "hit_rate": round(hits / total, 4) if total else 0.0}


def clear_query_cache() -> None:
    """清空查询向量缓存。换模型或做对照实验时用。"""
    with _lock:
        _query_cache.clear()
        _query_stats.update(hits=0, misses=0)
