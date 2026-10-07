#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地语义向量服务（由 sp_llm 环境运行，Supervisor 托管）。

职责：加载本地嵌入模型，对文本批次返回 L2 归一化向量。
- 只监听 127.0.0.1，文档内容不出本机。
- 模型或权重缺失时启动失败退出，由 Supervisor 重启；业务侧按服务不可用自动降级为词法检索。
- 与在线 API/Worker 进程隔离：重依赖（torch/transformers）只存在于本进程。

运行：sp_llm 环境 python scripts/embedding_server.py
配置：EMBED_MODEL_PATH（默认 00_base/models/bge-small-zh-v1.5）、
      EMBED_PORT（默认 18555）、EMBED_DEVICE（默认 cpu，可选 cuda:0）。
"""
import os
from pathlib import Path

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(os.environ.get("EMBED_MODEL_PATH", ROOT.parent / "models/bge-small-zh-v1.5"))
PORT = int(os.environ.get("EMBED_PORT", "18555"))
DEVICE = os.environ.get("EMBED_DEVICE", "cpu")
MAX_BATCH = 256
MAX_CHARS = 8000
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

app = FastAPI(title="ecom-embed", docs_url=None, redoc_url=None)
_tokenizer = None
_model = None
_dim = 0


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
    return {"status": "ok", "model": MODEL_PATH.name, "device": DEVICE, "dim": _dim}


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
