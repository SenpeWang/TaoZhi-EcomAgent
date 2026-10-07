"""本地语义向量服务客户端：HTTP 取向量，服务不可用时自动降级为词法检索。

模型与重依赖只存在于独立嵌入服务进程（sp_llm 环境，scripts/embedding_server.py），
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


def encode_passages(texts: list[str]) -> np.ndarray:
    """对资料切片编码，返回 (n, dim) 的 L2 归一化 float32 矩阵。"""
    if not texts:
        return np.zeros((0, 1), dtype="float32")
    return _post(list(texts), instruct=False)


def encode_query(query: str) -> np.ndarray:
    """对查询编码（附带检索指令前缀），返回 (1, dim) 向量。"""
    return _post([query], instruct=True)
