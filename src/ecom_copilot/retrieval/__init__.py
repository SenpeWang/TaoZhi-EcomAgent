"""RAG 检索层（PRD 模块 D）。"""

from .channels import (  # noqa: F401
    BM25Channel,
    BaseChannel,
    DenseChannel,
    GraphChannel,
    HyDEChannel,
    RetrievalHit,
    build_channels,
)
from .engine import RetrievalEngine, get_retrieval_engine  # noqa: F401
from .fusion import (  # noqa: F401
    QueryCache,
    lexical_overlap,
    reciprocal_rank_fusion,
    rerank,
)
from .index import KnowledgeIndex  # noqa: F401

__all__ = [
    "BM25Channel", "BaseChannel", "DenseChannel", "GraphChannel", "HyDEChannel",
    "RetrievalHit", "build_channels",
    "RetrievalEngine", "get_retrieval_engine",
    "QueryCache", "lexical_overlap", "reciprocal_rank_fusion", "rerank",
    "KnowledgeIndex",
]
