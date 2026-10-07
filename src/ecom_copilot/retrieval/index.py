"""本地知识索引：向量 + BM25 双索引，支持持久化（Milvus / ES 不可用时的降级底座）。"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from ..config import Settings, get_settings
from ..llm import get_embedding_provider, set_embedding_provider
from ..llm.embeddings import LocalTfidfSvdEmbedding
from ..schemas.common import TextChunk

try:  # 中文分词（接近 IK 分词效果）
    import jieba  # type: ignore

    def tokenize(text: str) -> List[str]:
        return [t for t in jieba.lcut(text) if t.strip() and len(t) > 1]
except Exception:  # noqa: BLE001

    def tokenize(text: str) -> List[str]:  # type: ignore[misc]
        return [t for t in re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z0-9\-]{2,}", text)]


class KnowledgeIndex:
    """进程内倒排 + 向量索引。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.chunks: List[TextChunk] = []
        self.vectors: Optional[np.ndarray] = None
        self._embedding_provider = None
        self.tokenized: List[List[str]] = []
        self._bm25 = None
        self._lock = threading.RLock()
        self.persist_dir = Path(self.settings.data_dir) / "index"
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    # ───────── 写入 ─────────
    def add(self, chunks: Sequence[TextChunk]) -> int:
        with self._lock:
            existing = {c.id for c in self.chunks}
            fresh = [c for c in chunks if c.id not in existing]
            if not fresh:
                return 0
            self.chunks.extend(fresh)
            self._rebuild()
            return len(fresh)

    def _rebuild(self) -> None:
        self.tokenized = [tokenize(c.text) for c in self.chunks]
        try:
            from rank_bm25 import BM25Okapi  # noqa: PLC0415

            self._bm25 = BM25Okapi(self.tokenized) if self.tokenized else None
        except Exception:  # noqa: BLE001
            self._bm25 = None
        self.vectors = self._encode([c.text for c in self.chunks])

    def _encode(self, texts: List[str]) -> Optional[np.ndarray]:
        if not texts:
            return None
        provider = self._embedding_provider or get_embedding_provider(self.settings)
        # New/edited enterprise vocabulary must be fitted; each index owns its vector space.
        if isinstance(provider, LocalTfidfSvdEmbedding):
            fresh = LocalTfidfSvdEmbedding(dim=self.settings.embedding_dim,
                                           cache_path=self.settings.data_dir / "embeddings" / "local_tfidf_svd.joblib")
            fresh.fit(texts)
            provider = fresh
        self._embedding_provider = provider
        try:
            return provider.encode(texts)
        except Exception:  # noqa: BLE001
            return None

    # ───────── 检索 ─────────
    def bm25_scores(self, query: str) -> np.ndarray:
        if self._bm25 is None or not self.chunks:
            return np.zeros(len(self.chunks), dtype="float32")
        terms=set(tokenize(query))
        scores=np.asarray(self._bm25.get_scores(list(terms)),dtype="float32")
        overlap=np.asarray([len(terms.intersection(words))/max(1,len(terms)) for words in self.tokenized],dtype="float32")
        # Small corpora can produce negative IDF; matching terms must remain retrievable.
        return np.maximum(scores,0)+overlap*.1

    def dense_scores(self, query: str) -> np.ndarray:
        if self.vectors is None or not self.chunks:
            return np.zeros(len(self.chunks), dtype="float32")
        provider = self._embedding_provider or get_embedding_provider(self.settings)
        try:
            qvec = provider.encode_one(query)
        except Exception:  # noqa: BLE001
            return np.zeros(len(self.chunks), dtype="float32")
        return np.asarray(self.vectors @ qvec.reshape(-1), dtype="float32")

    # ───────── 持久化 ─────────
    def save(self) -> None:
        with self._lock:
            payload = [c.model_dump(mode="json") for c in self.chunks]
            (self.persist_dir / ".chunks.json.new").write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
            import os
            os.replace(self.persist_dir/".chunks.json.new",self.persist_dir/"chunks.json")
            if self.vectors is not None:
                np.save(self.persist_dir / ".vectors.new.npy", self.vectors)
                os.replace(self.persist_dir/".vectors.new.npy",self.persist_dir/"vectors.npy")

    def load(self) -> int:
        chunks_path = self.persist_dir / "chunks.json"
        if not chunks_path.exists():
            return 0
        try:
            payload = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.chunks = [TextChunk.model_validate(item) for item in payload]
            vec_path = self.persist_dir / "vectors.npy"
            if vec_path.exists():
                self.vectors = np.load(vec_path)
            else:
                self.vectors = self._encode([c.text for c in self.chunks])
            self.tokenized = [tokenize(c.text) for c in self.chunks]
            try:
                from rank_bm25 import BM25Okapi  # noqa: PLC0415

                self._bm25 = BM25Okapi(self.tokenized) if self.tokenized else None
            except Exception:  # noqa: BLE001
                self._bm25 = None
            if isinstance(get_embedding_provider(self.settings),LocalTfidfSvdEmbedding):
                self.vectors=self._encode([c.text for c in self.chunks])
            return len(self.chunks)
        except Exception:  # noqa: BLE001
            return 0

    def clear(self) -> None:
        with self._lock:
            self.chunks = []
            self.vectors = None
            self.tokenized = []
            self._bm25 = None

    def __len__(self) -> int:
        return len(self.chunks)
