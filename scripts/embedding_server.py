#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地语义向量服务（由 llm 环境运行，Supervisor 托管）。

职责：加载本地嵌入模型，对文本批次返回 L2 归一化向量；提供 /rerank 交叉编码器精排。
- 只监听 127.0.0.1，文档内容不出本机。
- 嵌入模型或权重缺失时启动失败退出，由 Supervisor 重启；业务侧按服务不可用自动降级为词法检索。
- 精排模型惰性加载：权重缺失或加载失败不影响嵌入功能，/rerank 返回 503 由业务侧回落融合序。
- 只能跑在 GPU 上：指定设备不可用时启动即失败退出并给出明确原因，不做 CPU 降级
  （降级会让时延失真、掩盖真实故障）；运行中 GPU 掉线由看门狗探测后退出，交由 Supervisor 重启。
- 与在线 API/Worker 进程隔离：重依赖（torch/transformers）只存在于本进程。

运行：llm 环境 python scripts/embedding_server.py
配置：EMBED_MODEL_PATH（默认 00_base/models/bge-small-zh-v1.5）、
      EMBED_PORT（默认 18555）、EMBED_DEVICE（默认优先 GPU cuda:0，无可用 GPU 时才回退 cpu）、
      RERANK_MODEL_PATH（默认 00_base/models/bge-reranker-v2-m3）。
"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(os.environ.get("EMBED_MODEL_PATH", ROOT.parent / "models/bge-small-zh-v1.5"))
RERANK_MODEL_PATH = Path(os.environ.get("RERANK_MODEL_PATH", ROOT.parent / "models/bge-reranker-v2-m3"))
PORT = int(os.environ.get("EMBED_PORT", "18555"))
# 全面使用 GPU：显式指定优先，否则自动优先 cuda:0，仅在无可用 GPU 时回退 cpu
DEVICE = os.environ.get("EMBED_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
# 指定 GPU 却拿不到 → 直接失败退出，不做 CPU 降级：
# 降级会让链路"看起来正常"但时延与吞吐完全失真（精排从百毫秒掉到数十秒），
# 掩盖真实故障。宁可让 Supervisor 报错重启，由运维看到并修驱动/直通。
if DEVICE.startswith("cuda") and not torch.cuda.is_available():
    raise RuntimeError(
        f"指定推理设备 {DEVICE} 不可用：当前无可用 CUDA GPU"
        f"（检查显卡驱动、WSL GPU 直通与显存占用）；如需 CPU 运行请显式设置 EMBED_DEVICE=cpu"
    )
MAX_BATCH = 256
MAX_CHARS = 8000
RERANK_MAX_BATCH = 64
RERANK_MAX_CHARS = 4000
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
GPU_WATCHDOG_INTERVAL = 30.0


def _gpu_probe() -> None:
    """用 nvidia-smi 独立探测 GPU（不碰已可能损坏的 CUDA 上下文）。不可用则抛异常。

    只依赖 PATH 查找（WSL 下由系统 PATH 提供），需要时用 EMBED_GPU_PROBE_BIN 覆盖，
    不把任何机器相关的绝对路径写死在代码里。
    """
    exe = os.environ.get("EMBED_GPU_PROBE_BIN") or shutil.which("nvidia-smi")
    if not exe:
        raise RuntimeError("PATH 中找不到 nvidia-smi，无法探测 GPU（可用 EMBED_GPU_PROBE_BIN 指定）")
    r = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"],
                       capture_output=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode(errors="ignore").strip()[:120] or "nvidia-smi 非零退出")


def gpu_watchdog() -> None:
    """GPU 掉线看门狗。

    WSL 的 GPU 直通会在宿主挂起、驱动更新后消失。此时**已建立的 CUDA 上下文会静默挂死**：
    进程占满 CPU、端口还在监听、但请求永不返回——比启动失败隐蔽得多，业务侧只会看到
    "检索/精排超时"。启动校验拦不住这种运行中掉线，所以必须持续探测。

    探测到掉线就退出进程，交由 Supervisor 重启；重启时若 GPU 仍未恢复，
    启动校验会立即失败并给出明确错误，而不是继续假装服务正常。
    """
    while True:
        time.sleep(GPU_WATCHDOG_INTERVAL)
        try:
            _gpu_probe()
        except Exception as exc:
            print(f"[embed] GPU 探测失败（{type(exc).__name__}: {exc}），退出进程交由 Supervisor 重启", flush=True)
            os._exit(1)

app = FastAPI(title="ecom-embed", docs_url=None, redoc_url=None)
_tokenizer = None
_model = None
_dim = 0
_rerank_tokenizer = None
_rerank_model = None
_rerank_failed = False


def load_model():
    global _tokenizer, _model, _dim
    if _model is not None:
        return
    if not (MODEL_PATH / "config.json").is_file():
        raise RuntimeError(f"嵌入模型权重不存在：{MODEL_PATH}")
    _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_PATH))
    _model = AutoModel.from_pretrained(str(MODEL_PATH)).to(DEVICE).eval()
    _dim = _model.config.hidden_size


@torch.no_grad()
def encode(texts: list[str], instruct: bool) -> list[list[float]]:
    prepared = [(QUERY_INSTRUCTION + t) if instruct else t for t in texts]
    out = []
    for start in range(0, len(prepared), 32):
        batch = prepared[start:start + 32]
        enc = _tokenizer(batch, padding=True, truncation=True, max_length=512, return_tensors="pt").to(DEVICE)
        hidden = _model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        out.extend(pooled.cpu().float().tolist())
    return out


class EmbedRequest(BaseModel):
    texts: list[str]
    instruct: bool = False


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_PATH.name, "device": DEVICE, "dim": _dim,
            "rerank": RERANK_MODEL_PATH.name if _rerank_model is not None else None}


def load_reranker():
    """惰性加载精排模型；权重缺失或加载失败时置失败标记，后续请求返回 503。"""
    global _rerank_tokenizer, _rerank_model, _rerank_failed
    if _rerank_model is not None or _rerank_failed:
        return
    try:
        if not (RERANK_MODEL_PATH / "config.json").is_file():
            raise RuntimeError(f"精排模型权重不存在：{RERANK_MODEL_PATH}")
        _rerank_tokenizer = AutoTokenizer.from_pretrained(str(RERANK_MODEL_PATH))
        # 同样不做 CPU 降级：设备不可用时置失败标记，/rerank 返回 503，
        # 由检索侧回落融合序（精排是增强项，不拖垮主链路）。
        _rerank_model = AutoModelForSequenceClassification.from_pretrained(str(RERANK_MODEL_PATH)).to(DEVICE).eval()
    except Exception as exc:
        print(f"[embed] 精排模型不可用（{type(exc).__name__}: {exc}）", flush=True)
        _rerank_failed = True
        _rerank_tokenizer = None


@torch.no_grad()
def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """交叉编码器打分：sigmoid(单 logit) 相关性概率，输入顺序对齐返回。"""
    out = []
    for start in range(0, len(texts), 16):
        batch = texts[start:start + 16]
        enc = _rerank_tokenizer([query] * len(batch), batch, padding=True, truncation=True,
                                max_length=512, return_tensors="pt").to(DEVICE)
        logits = _rerank_model(**enc).logits.reshape(-1)
        out.extend(torch.sigmoid(logits.float()).cpu().tolist())
    return out


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    top_n: int | None = None


@app.post("/rerank")
def rerank(req: RerankRequest):
    load_reranker()
    if _rerank_model is None:
        raise HTTPException(status_code=503, detail="精排模型不可用")
    texts = req.texts
    if not req.query.strip() or not texts or len(texts) > RERANK_MAX_BATCH:
        raise HTTPException(status_code=400, detail="query 非空且 texts 须为 1 至 64 条")
    if any(not isinstance(t, str) or not t.strip() or len(t) > RERANK_MAX_CHARS for t in texts):
        raise HTTPException(status_code=400, detail="单条文本须为非空且不超过 4000 字符")
    scores = rerank_scores(req.query, texts)
    if req.top_n is not None:
        order = np.argsort(-np.asarray(scores, dtype="float32"))[:req.top_n]
        return {"scores": [scores[int(i)] for i in order], "indices": [int(i) for i in order]}
    return {"scores": scores}


@app.post("/embed")
def embed(req: EmbedRequest):
    texts = req.texts
    if not texts or len(texts) > MAX_BATCH:
        raise HTTPException(status_code=400, detail="texts 须为 1 至 256 条")
    if any(not isinstance(t, str) or not t.strip() or len(t) > MAX_CHARS for t in texts):
        raise HTTPException(status_code=400, detail="单条文本须为非空且不超过 8000 字符")
    return {"vectors": encode(texts, req.instruct), "dim": _dim}


if __name__ == "__main__":
    load_model()
    if DEVICE.startswith("cuda"):
        threading.Thread(target=gpu_watchdog, daemon=True).start()
        print(f"[embed] 设备 {DEVICE}，GPU 看门狗已启动（每 {GPU_WATCHDOG_INTERVAL:.0f}s 探测一次）", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
