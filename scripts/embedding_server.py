#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地语义向量服务（由 llm 环境运行，Supervisor 托管）。

职责：加载本地嵌入模型，对文本批次返回 L2 归一化向量；提供 /rerank 交叉编码器精排。
- 只监听 127.0.0.1，文档内容不出本机。
- 嵌入模型或权重缺失时启动失败退出，由 Supervisor 重启；业务侧按服务不可用自动降级为词法检索。
- 精排模型惰性加载：权重缺失或加载失败不影响嵌入功能，/rerank 返回 503 由业务侧回落融合序。
- 与在线 API/Worker 进程隔离：重依赖（torch/transformers）只存在于本进程。

运行：llm 环境 python scripts/embedding_server.py
配置：EMBED_MODEL_PATH（默认 00_base/models/bge-small-zh-v1.5）、
      EMBED_PORT（默认 18555）、EMBED_DEVICE（默认优先 GPU cuda:0，无可用 GPU 时才回退 cpu）、
      RERANK_MODEL_PATH（默认 00_base/models/bge-reranker-v2-m3）。
"""
import os
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
MAX_BATCH = 256
MAX_CHARS = 8000
RERANK_MAX_BATCH = 64
RERANK_MAX_CHARS = 4000
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

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
        _rerank_model = AutoModelForSequenceClassification.from_pretrained(str(RERANK_MODEL_PATH)).to(DEVICE).eval()
    except Exception:
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
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
