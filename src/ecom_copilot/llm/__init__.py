"""LLM 服务层。"""

from .client import LLMClient, ModelTier, get_llm  # noqa: F401
from .embeddings import (  # noqa: F401
    EmbeddingProvider,
    HashingEmbedding,
    LocalTfidfSvdEmbedding,
    RemoteEmbedding,
    SentenceTransformerEmbedding,
    cosine_matrix,
    embed_one,
    embed_texts,
    get_embedding_provider,
    set_embedding_provider,
)
from .json_utils import extract_json, parse_model  # noqa: F401

__all__ = [
    "LLMClient", "ModelTier", "get_llm",
    "EmbeddingProvider", "HashingEmbedding", "LocalTfidfSvdEmbedding",
    "RemoteEmbedding", "SentenceTransformerEmbedding", "cosine_matrix",
    "embed_one", "embed_texts", "get_embedding_provider", "set_embedding_provider",
    "extract_json", "parse_model",
]
