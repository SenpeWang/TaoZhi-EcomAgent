"""RAG 能力层：离线数据管道、在线多路检索与独立评估。"""
from .retrieval import ContextBuilder, MultiRetriever

__all__ = ["ContextBuilder", "MultiRetriever"]