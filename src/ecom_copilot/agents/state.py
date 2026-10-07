"""LangGraph 1.0 状态定义与共享运行时上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

from ..config import Settings, get_settings
from ..graph.builder import IndustryGraphBuilder
from ..hypothesis.tracker import HypothesisTracker
from ..retrieval.engine import RetrievalEngine
from ..schemas.common import (
    Citation,
    Evidence,
    PermissionContext,
    SourceDocument,
    TextChunk,
)
from ..schemas.graph import ExtractionResult
from ..schemas.research import (
    DebateMatrix,
    Hypothesis,
    ResearchReport,
    ResearchTask,
    TaskPlan,
)


class ResearchState(TypedDict, total=False):
    """商品问答任务全局状态（可被 LangGraph 1.0 checkpointer 持久化）。"""

    task: ResearchTask
    permission: PermissionContext
    plan: Optional[TaskPlan]

    # 模块 A
    documents: List[SourceDocument]
    # 模块 B
    chunks: List[TextChunk]
    parse_summary: Dict[str, Any]
    # 模块 C
    extraction: Optional[ExtractionResult]
    graph_result: Dict[str, Any]
    # 模块 D
    evidence_pool: List[Evidence]
    retrieval_report: Dict[str, Any]
    # 模块 E
    hypotheses: List[Hypothesis]
    debate: Optional[DebateMatrix]
    research_round: int
    # 模块 F
    report: Optional[ResearchReport]
    citations: List[Citation]

    specialists: List[Dict[str, Any]]
    quality: Dict[str, Any]
    business: Dict[str, Any]

    # 编排
    route: str
    iteration: int
    messages: List[Dict[str, Any]]
    trace: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    errors: List[str]
    review_decision: str
    # 会话记忆：入口处注入的「【会话历史】」文本块（供指代消解，默认空串向后兼容）
    session_history: str


@dataclass
class ResearchRuntime:
    """非序列化运行时：检索引擎 / 图谱 / 追踪器 / 配置。"""

    task_id: str
    settings: Settings = field(default_factory=get_settings)
    engine: Optional[RetrievalEngine] = None
    graph_builder: Optional[IndustryGraphBuilder] = None
    tracker: Optional[HypothesisTracker] = None
    tracer: Any = None

    def __post_init__(self) -> None:
        if self.engine is None:
            self.engine = RetrievalEngine(self.settings)
        if self.graph_builder is None:
            self.graph_builder = IndustryGraphBuilder(settings=self.settings)
        if self.tracker is None:
            self.tracker = HypothesisTracker(self.task_id, self.settings)

    @property
    def permission(self) -> PermissionContext:
        return PermissionContext(user_id=self.task_id, org_tag="")


def initial_state(task: ResearchTask,
                  permission: Optional[PermissionContext] = None) -> ResearchState:
    return ResearchState(
        task=task,
        permission=permission
        or PermissionContext(user_id=task.user_id, org_tag=task.org_tag),
        plan=None,
        documents=[],
        chunks=[],
        parse_summary={},
        extraction=None,
        graph_result={},
        evidence_pool=[],
        retrieval_report={},
        hypotheses=[],
        debate=None,
        research_round=0,
        report=None,
        citations=[],
        route="supervisor",
        iteration=0,
        messages=[],
        trace=[],
        metrics={},
        errors=[],
        review_decision="",
        session_history="",
    )


def append_trace(state: ResearchState, agent: str, summary: str,
                 elapsed: float = 0.0, **extra) -> None:
    state.setdefault("trace", []).append(
        {"agent": agent, "summary": summary, "elapsed_s": round(elapsed, 3), **extra}
    )
