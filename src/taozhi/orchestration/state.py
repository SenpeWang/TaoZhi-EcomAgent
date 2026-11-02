"""编排层全局状态契约：驱动 5 节点迭代闭环状态机流转。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..schemas.rag import CitationItem, RetrievalCandidate


class EvaluationResult(BaseModel):
    """检索评估节点判定结果。"""
    is_sufficient: bool = False
    score: float = 0.0
    reason: str = ""
    missing_aspects: List[str] = Field(default_factory=list)


class OrchestrationState(BaseModel):
    """编排引擎在各个状态机节点之间流转的确定性上下文快照。"""
    session_id: str
    trace_id: str
    tenant_id: str = "default"
    user_id: Optional[str] = None
    security_level: int = 0

    original_query: str
    current_query: str
    rewrite_history: List[str] = Field(default_factory=list)
    iteration_count: int = 0
    max_iterations: int = 3

    candidates: List[RetrievalCandidate] = Field(default_factory=list)
    evaluation: EvaluationResult = Field(default_factory=EvaluationResult)

    answer: str = ""
    citations: List[CitationItem] = Field(default_factory=list)
    is_fallback: bool = False
    next_node: str = "decide"
    metadata: Dict[str, Any] = Field(default_factory=dict)