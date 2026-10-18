"""本地语义向量服务客户端：HTTP 取向量，服务不可用时自动降级为词法检索。

模型与重依赖只存在于独立嵌入服务进程（llm 环境，scripts/embedding_server.py），
本模块不引入 torch/transformers。文档内容只发往 127.0.0.1 回环地址。
"""
import threading
import time

import httpx
import numpy as np

from .config import load_config

_lock = threading.Lock()
_state = {"checked_at": 0.0, "ok": False}
OK_TTL = 10.0
FAIL_TTL = 10.0


def available() -> bool:
    """探测嵌入服务健康状态，结果短时间缓存，避免每条查询都探测。"""
    now = time.monotonic()
    with _lock:
        if now < _state["checked_at"]:
            return _state["ok"]
    ok = False
    try:
        r = httpx.get(load_config().embed_service_url.rstrip("/") + "/health", timeout=1.5, trust_env=False)
        ok = r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        ok = False
    with _lock:
        _state["ok"] = ok
        _state["checked_at"] = now + (OK_TTL if ok else FAIL_TTL)
    return ok


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
    """对资料切片编码，返回 (n, dim) 的 L2 归一化 float32 矩阵。"""
    if not texts:
        return np.zeros((0, 1), dtype="float32")
    return _post(list(texts), instruct=False)


def encode_query(query: str) -> np.ndarray:
    """对查询编码（附带检索指令前缀），返回 (1, dim) 向量。"""
    return _post([query], instruct=True)


def rerank(query: str, texts: list[str]) -> list[float]:
    """交叉编码器精排打分：返回与 texts 输入顺序对齐的相关性概率（0-1）。

    服务不可用或精排模型缺失（503）时抛异常，由调用方回落融合序。
    """
    r = httpx.post(load_config().embed_service_url.rstrip("/") + "/rerank",
                   json={"query": query, "texts": list(texts)},
                   timeout=load_config().rerank_timeout, trust_env=False)
    r.raise_for_status()
    return [float(s) for s in r.json()["scores"]]
