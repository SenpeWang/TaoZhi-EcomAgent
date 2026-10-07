"""Embedding 层：远端 OpenAI 兼容端点 → sentence-transformers → 本地 TF-IDF+SVD 降级。

PRD 选型为 BGE-M3（dense）+ Qwen3-VL-Embedding（多模态）。
当部署环境无法访问这些服务时，自动降级为本地可持久化的
字符 n-gram TF-IDF + TruncatedSVD 语义向量，保证离线可跑。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from ..config import Settings, get_settings

# 商品域种子词：无外部语料时保证降级向量空间覆盖商品知识核心词汇
_SEED_TERMS = ("钢化膜", "手机壳", "贴膜神器", "膜切机", "UV打印机", "机型适配",
               "防窥", "疏油层", "9H", "刀模", "喷头", "保修",
               "适配", "SKU", "型号")


class EmbeddingProvider:
    """统一接口：encode(texts) -> (n, dim) float32 归一化矩阵。"""

    name: str = "base"
    dim: int = 0

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (matrix / norms).astype("float32")


class RemoteEmbedding(EmbeddingProvider):
    """OpenAI 兼容 /embeddings 端点（BGE-M3 / Qwen3-VL-Embedding 等）。"""

    name = "remote"

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 1024) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        import httpx

        resp = httpx.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": list(texts)},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vectors = np.array([row["embedding"] for row in data], dtype="float32")
        self.dim = vectors.shape[1]
        return self._normalize(vectors)


class SentenceTransformerEmbedding(EmbeddingProvider):
    """本地 sentence-transformers（BGE-M3 / Qwen3-Embedding）。"""

    name = "sentence-transformers"

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model_name, device=device)
        self.dim = self._model.get_sentence_embedding_dimension() or 1024

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(list(texts), normalize_embeddings=True, batch_size=16)
        return np.asarray(vectors, dtype="float32")


class LocalTfidfSvdEmbedding(EmbeddingProvider):
    """离线兜底：字符 n-gram TF-IDF + TruncatedSVD（LSA）稠密向量。

    对中文商品文本（商品型号 / 参数 / 配件名称）具备稳定的字符级语义表征能力，
    且无需下载任何模型权重，可直接持久化复用。
    """

    name = "local-tfidf-svd"

    def __init__(self, dim: int = 256, cache_path: Optional[Path] = None) -> None:
        self.dim = dim
        self.cache_path = Path(cache_path) if cache_path else None
        self._vectorizer = None
        self._svd = None
        self._lock = threading.RLock()
        if self.cache_path and self.cache_path.exists():
            self._load()

    # ─────── 持久化 ───────
    def _load(self) -> None:
        try:
            import joblib  # noqa: PLC0415

            payload = joblib.load(self.cache_path)
            self._vectorizer, self._svd = payload["vectorizer"], payload["svd"]
            self.dim = int(self._svd.n_components)
        except Exception:  # noqa: BLE001
            self._vectorizer = self._svd = None

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            import joblib  # noqa: PLC0415

            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({"vectorizer": self._vectorizer, "svd": self._svd}, self.cache_path)
        except Exception:  # noqa: BLE001
            pass

    @property
    def fitted(self) -> bool:
        return self._vectorizer is not None and self._svd is not None

    def fit(self, texts: Sequence[str]) -> None:
        from sklearn.decomposition import TruncatedSVD  # noqa: PLC0415
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415

        corpus = [t if t.strip() else " " for t in texts]
        if len(corpus) < 2:
            corpus = corpus + list(_SEED_TERMS)
        self._vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True
        )
        tfidf = self._vectorizer.fit_transform(corpus)
        n_components = min(self.dim, min(tfidf.shape) - 1)
        n_components = max(2, n_components)
        self._svd = TruncatedSVD(n_components=n_components, random_state=42)
        self._svd.fit(tfidf)
        self.dim = n_components
        self._save()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        with self._lock:
            if not self.fitted:
                self.fit(texts)
            tfidf = self._vectorizer.transform([t if t.strip() else " " for t in texts])
            return self._normalize(np.asarray(self._svd.transform(tfidf), dtype="float32"))


class HashingEmbedding(EmbeddingProvider):
    """零依赖兜底：特征哈希 + 词袋。仅在 sklearn 不可用时使用。"""

    name = "hashing"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            for j in range(len(text) - 1):
                gram = text[j:j + 2]
                digest = hashlib.md5(gram.encode("utf-8")).hexdigest()
                matrix[i, int(digest[:8], 16) % self.dim] += 1.0
        return self._normalize(matrix)


_provider: Optional[EmbeddingProvider] = None
_provider_lock = threading.RLock()


def build_provider(settings: Optional[Settings] = None, corpus: Sequence[str] = ()) -> EmbeddingProvider:
    cfg = settings or get_settings()
    cache_path = cfg.data_dir / "embeddings" / "local_tfidf_svd.joblib"

    if cfg.embedding_base_url and cfg.embedding_api_key:
        try:
            provider = RemoteEmbedding(
                cfg.embedding_base_url, cfg.embedding_api_key, cfg.embedding_model, cfg.embedding_dim
            )
            provider.encode(["连通性探测"])
            return provider
        except Exception:  # noqa: BLE001
            pass

    try:
        return SentenceTransformerEmbedding(cfg.embedding_model, cfg.embedding_device)
    except Exception:  # noqa: BLE001
        pass

    try:
        provider = LocalTfidfSvdEmbedding(dim=cfg.embedding_dim, cache_path=cache_path)
        if corpus:
            provider.fit(corpus)
        else:
            provider.encode(list(_SEED_TERMS))
        return provider
    except Exception:  # noqa: BLE001
        return HashingEmbedding(dim=cfg.embedding_dim)


def get_embedding_provider(settings: Optional[Settings] = None,
                           corpus: Sequence[str] = ()) -> EmbeddingProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = build_provider(settings, corpus)
        return _provider


def set_embedding_provider(provider: EmbeddingProvider) -> None:
    global _provider
    with _provider_lock:
        _provider = provider


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    return get_embedding_provider().encode(list(texts))


def embed_one(text: str) -> np.ndarray:
    return get_embedding_provider().encode_one(text)


def cosine_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros(0, dtype="float32")
    return np.asarray(matrix @ query.reshape(-1), dtype="float32")
