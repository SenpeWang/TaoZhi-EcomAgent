"""LangGraph 1.0 编排：Supervisor + 6 个 Agent，支持持久化与 human-in-the-loop。"""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator, Dict, Iterator, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..config import Settings, get_settings
from ..graph.builder import IndustryGraphBuilder
from ..hypothesis.tracker import HypothesisTracker
from ..memory import HistoryInjector, get_faq_cache, get_session_store
from ..observability import TaskTracer, enable_langsmith, record_agent, write_audit
from ..retrieval.engine import RetrievalEngine, get_retrieval_engine
from ..schemas.common import Citation, PermissionContext
from ..schemas.research import (
    ReportSection,
    ResearchReport,
    ResearchTask,
    TaskStatus,
)
from .data_collection import data_collection_node
from .deep_research import deep_research_node
from .doc_parsing import doc_parsing_node
from .graph_building import graph_building_node
from .knowledge_extraction import knowledge_extraction_node
from .report_generation import report_generation_node
from .state import ResearchRuntime, ResearchState, initial_state
from .supervisor import route_next, supervisor_node
from .specialists import specialist_review_node
from .quality_gate import quality_gate_node, quality_assessment_node

AGENT_NODES = {
    "data_collection": data_collection_node,
    "doc_parsing": doc_parsing_node,
    "knowledge_extraction": knowledge_extraction_node,
    "graph_building": graph_building_node,
    "deep_research": deep_research_node,
    "report_generation": report_generation_node,
    "specialist_review": specialist_review_node,
}


def _build_checkpointer(settings: Settings):
    """durable execution：优先 SQLite 持久化，降级为内存。"""
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(str(settings.checkpoint_db), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:  # noqa: BLE001
        if settings.app_env.lower() in ("prod","production"):
            raise RuntimeError("Production requires persistent SQLite checkpoints") from None
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()


def build_graph(settings: Optional[Settings] = None):
    settings = settings or get_settings()
    builder = StateGraph(ResearchState)
    builder.add_node("supervisor", supervisor_node)
    for name, node in AGENT_NODES.items():
        builder.add_node(name, node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_next,
        {name: name for name in AGENT_NODES} | {"end": END},
    )
    builder.add_node("quality_assessment",quality_assessment_node)
    builder.add_node("quality_gate", quality_gate_node)
    for name in AGENT_NODES:
        builder.add_edge(name, "quality_assessment" if name == "report_generation" else "supervisor")
    builder.add_edge("quality_assessment","quality_gate")
    builder.add_edge("quality_gate", END)

    return builder.compile(checkpointer=_build_checkpointer(settings))


class ResearchWorkflow:
    """商品问答工作流门面：同步执行 / 流式执行 / 断点恢复。"""

    def __init__(self, settings: Optional[Settings] = None,
                 engine: Optional[RetrievalEngine] = None,
                 graph_builder: Optional[IndustryGraphBuilder] = None) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or get_retrieval_engine(self.settings)
        self.engine.load()
        self.graph_builder = graph_builder or IndustryGraphBuilder(
            store=self.engine.graph_store, settings=self.settings
        )
        self.graph = build_graph(self.settings)
        self.tracers: Dict[str, TaskTracer] = {}
        self.results: Dict[str, ResearchState] = {}
        enable_langsmith()

    # ───────── 运行时 ─────────
    def _runtime(self, task: ResearchTask) -> ResearchRuntime:
        tracer = TaskTracer(trace_id=task.task_id, name="qa_pipeline")
        self.tracers[task.task_id] = tracer
        tracker = HypothesisTracker(task.task_id, self.settings)
        return ResearchRuntime(
            task_id=task.task_id,
            settings=self.settings,
            engine=self.engine,
            graph_builder=self.graph_builder,
            tracker=tracker,
            tracer=tracer,
        )

    def _config(self, runtime: ResearchRuntime) -> RunnableConfig:
        return {
            "configurable": {"thread_id": runtime.task_id, "runtime": runtime},
            "recursion_limit": 60,
        }

    # ───────── 任务入口（run / stream 共用） ─────────
    def _entry_state(self, task: ResearchTask,
                     permission: Optional[PermissionContext] = None) -> ResearchState:
        """初始状态构造 + 会话历史注入（session_id 取 user_id，org 隔离由 org_tag 记录）。

        记忆属于旁路能力：MEMORY_ENABLED=false 或 store 异常时静默跳过。
        """
        state = initial_state(task, permission)
        if self.settings.retrieval_first_enabled and (len(self.engine.index) or self.settings.auth_enabled):
            # Serving reuses the authorized index; ingestion/extraction is not repeated per question.
            from ..schemas.common import SourceDocument
            from ..schemas.graph import ExtractionResult
            hits=self.engine.retrieve(task.question,top_k=12,permission=state["permission"])
            if hits or self.settings.auth_enabled:
                chunks=[h.chunk for h in hits]
                docs={}
                for c in chunks:
                    docs.setdefault(c.doc_id,SourceDocument(id=c.doc_id,title=c.doc_name,
                        raw_text=c.text,source=c.source,owner_id=c.owner_id,org_tag=c.org_tag,
                        is_public=c.is_public,meta=c.meta))
                state.update(documents=list(docs.values()),chunks=chunks,extraction=ExtractionResult(),
                    graph_result={"mode":"retrieval_first","authorized_chunks":len(chunks)},
                    evidence_pool=[h.to_evidence() for h in hits])
        if self.settings.memory_enabled and not self.settings.auth_enabled:
            try:
                state["session_history"] = HistoryInjector.build(
                    session_id=_session_scope(task), question=task.question
                )
            except Exception:  # noqa: BLE001 — 记忆故障绝不打断主流程
                state["session_history"] = ""
        return state

    def _cache_hit_state(self, task: ResearchTask) -> Optional[Dict[str, Any]]:
        """FAQ 语义缓存命中时构造最小成功 state（跳过完整 6 Agent 流程）。"""
        return None  # Legacy semantic cache is disabled until ACL/revision migration.

    def _finish_cache_hit(self, task: ResearchTask, merged: Dict[str, Any],
                          started: float) -> Dict[str, Any]:
        """缓存命中路径的收尾：状态置完成 + 审计 + 会话写回。"""
        elapsed = time.perf_counter() - started
        report = merged.get("report")
        if report is not None:
            report.elapsed_seconds = round(elapsed, 2)
        task.status = TaskStatus.COMPLETED
        merged["task"] = task
        merged["metrics"] = {**merged.get("metrics", {}), "total_elapsed_s": round(elapsed, 2)}
        write_audit({
            "task_id": task.task_id,
            "user_id": task.user_id,
            "org_tag": task.org_tag,
            "question": task.question,
            "status": task.status.value,
            "elapsed_s": round(elapsed, 2),
            "faq_cache_hit": True,
        })
        self._record_turn(task, merged, cache_hit=True)
        return merged

    def _record_turn(self, task: ResearchTask, final_state: Dict[str, Any],
                     cache_hit: bool = False) -> None:
        """任务完成写回本轮 Q-A；拒答与缓存命中结果不回写 FAQ 缓存。任何异常静默。"""
        if not self.settings.memory_enabled or self.settings.auth_enabled or task.status != TaskStatus.COMPLETED:
            return
        try:
            answer = _answer_text(final_state)
            if not answer:
                return
            store = get_session_store()
            store.add_message(session_id=_session_scope(task), role="user", content=task.question,
                              user_id=task.user_id, org_tag=task.org_tag, task_id=task.task_id)
            store.add_message(session_id=_session_scope(task), role="assistant", content=answer,
                              user_id=task.user_id, org_tag=task.org_tag, task_id=task.task_id)
            report = final_state.get("report")
            if cache_hit or report is None or "暂时无法回答" in str(getattr(report, "title", "")):
                return
            citations = [c.model_dump() for c in getattr(report, "citations", [])]
            # Do not publish tenant-private answers into the legacy global cache.
        except Exception:  # noqa: BLE001 — 记忆故障绝不打断主流程
            pass

    # ───────── 同步执行 ─────────
    def run(self, task: ResearchTask,
            permission: Optional[PermissionContext] = None) -> ResearchState:
        for _ in self.stream(task, permission):
            pass
        return self.results[task.task_id]

    # ───────── 流式执行（SSE） ─────────
    def stream(self, task: ResearchTask,
               permission: Optional[PermissionContext] = None) -> Iterator[Dict[str, Any]]:
        started = time.perf_counter()
        yield {"stage": "start", "progress": 0.0, "task_id": task.task_id}
        # FAQ 语义缓存命中：跳过完整流程直接返回
        cached = self._cache_hit_state(task)
        if cached is not None:
            merged = self._finish_cache_hit(task, cached, started)
            self.results[task.task_id] = ResearchState(**merged)  # type: ignore[arg-type]
            elapsed = merged["metrics"]["total_elapsed_s"]
            yield {"stage": "faq_cache_hit", "progress": 1.0,
                   "task_id": task.task_id, "elapsed_s": elapsed}
            yield {"stage": "done", "progress": 1.0, "task_id": task.task_id,
                   "status": task.status.value, "elapsed_s": elapsed}
            return
        runtime = self._runtime(task)
        state = self._entry_state(task, permission)
        task.status = TaskStatus.PROCESSING
        merged: Dict[str, Any] = dict(state)
        for chunk in self.graph.stream(state, self._config(runtime), stream_mode="updates"):
            if time.perf_counter()-started > self.settings.task_timeout_seconds:
                raise TimeoutError("task_deadline_exceeded")
            for node_name, update in chunk.items():
                if isinstance(update, dict):
                    merged.update(update)
                if node_name == "supervisor":
                    continue
                payload = _stage_payload(node_name, update)
                payload["progress"] = _progress(node_name)
                payload["elapsed_s"] = round(time.perf_counter() - started, 2)
                yield payload
        snapshot = self.graph.get_state(self._config(runtime))
        if snapshot.values:
            merged.update(snapshot.values)
        paused = bool(snapshot.next)
        merged["task"] = task
        elapsed = time.perf_counter() - started
        merged["metrics"] = {**(merged.get("metrics") or {}),
                             "total_elapsed_s": round(elapsed, 2)}
        if paused:
            task.status = TaskStatus.AWAITING_REVIEW
        elif merged.get("report") is not None:
            merged["report"].elapsed_seconds = round(elapsed, 2)
            task.status = TaskStatus.COMPLETED
        else:
            task.status = TaskStatus.FAILED
        self._record_turn(task, merged)
        self.results[task.task_id] = ResearchState(**merged)  # type: ignore[arg-type]
        yield {"stage": "done", "progress": 1.0, "task_id": task.task_id,
               "status": task.status.value, "elapsed_s": round(elapsed, 2)}

    async def astream(self, task: ResearchTask,
                      permission: Optional[PermissionContext] = None) -> AsyncIterator[Dict[str, Any]]:
        import asyncio

        loop = asyncio.get_running_loop()

        def _produce():
            return list(self.stream(task, permission))

        events = await loop.run_in_executor(None, _produce)
        for event in events:
            yield event

    # ───────── human-in-the-loop 恢复 ─────────
    def resume(self, task: ResearchTask, decision: str) -> ResearchState:
        runtime = self._runtime(task)
        config = self._config(runtime)
        snapshot = self.graph.get_state(config)
        if not snapshot.next:
            raise ValueError("No pending checkpoint for human review")
        final_state: Dict[str, Any] = dict(snapshot.values)
        for chunk in self.graph.stream(Command(resume=decision), config, stream_mode="updates"):
            for _node, update in chunk.items():
                if isinstance(update, dict):
                    final_state.update(update)
        snapshot = self.graph.get_state(config)
        final_state.update(snapshot.values)
        task.status = TaskStatus.AWAITING_REVIEW if snapshot.next else TaskStatus.COMPLETED
        final_state["task"] = task
        self.results[task.task_id] = ResearchState(**final_state)
        if task.status == TaskStatus.COMPLETED:
            self._record_turn(task, final_state)
        return ResearchState(**final_state)

    def get_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        try:
            snapshot = self.graph.get_state(
                {"configurable": {"thread_id": task_id}}
            )
            return {
                "values": snapshot.values,
                "next": list(snapshot.next or []),
                "tasks": [t.name for t in getattr(snapshot, "tasks", []) or []],
            }
        except Exception:  # noqa: BLE001
            return None


def _stage_payload(node_name: str, update: Dict[str, Any]) -> Dict[str, Any]:
    if node_name == "data_collection":
        return {"stage": "data_collection",
                "sources_queried": (update.get("metrics") or {}).get("subagents", 0),
                "documents": len(update.get("documents") or [])}
    if node_name == "doc_parsing":
        return {"stage": "doc_parsing", **(update.get("parse_summary") or {})}
    if node_name == "knowledge_extraction":
        extraction = update.get("extraction")
        return {"stage": "knowledge_extraction",
                "entities": len(getattr(extraction, "entities", []) or []),
                "triples": len(getattr(extraction, "triples", []) or [])}
    if node_name == "graph_building":
        result = update.get("graph_result") or {}
        return {"stage": "graph_building",
                "new_nodes": result.get("new_nodes", 0),
                "new_edges": result.get("new_edges", 0)}
    if node_name == "deep_research":
        return {"stage": "hypothesis_tracking",
                "round": update.get("research_round", 0),
                "hypotheses": [
                    {"text": h.hypothesis_text, "status": h.status.value,
                     "confidence": h.confidence}
                    for h in (update.get("hypotheses") or [])
                ]}
    if node_name == "quality_assessment":
        return {"stage":"quality_assessment","quality":update.get("quality",{})}
    if node_name == "report_generation":
        report = update.get("report")
        return {"stage": "report_generation",
                "title": getattr(report, "title", ""),
                "citation_coverage": getattr(report, "citation_coverage", 0.0),
                "review_required": getattr(report, "review_required", False)}
    return {"stage": node_name}


def _progress(node_name: str) -> float:
    order = {"data_collection": 0.25, "doc_parsing": 0.4, "knowledge_extraction": 0.55,
             "graph_building": 0.7, "deep_research": 0.85, "report_generation": 0.95}
    order.update(specialist_review=0.9,quality_assessment=0.98,quality_gate=0.99)
    return order.get(node_name, 0.1)


def _answer_text(final_state: Dict[str, Any]) -> str:
    """取最终答案文本：优先结论先行答案卡，缺省回退报告 markdown。"""
    report = final_state.get("report")
    if report is None:
        return ""
    text = str(getattr(report, "executive_summary", "") or "").strip()
    return text or report.to_markdown()


def _citation_from(payload: Dict[str, Any]) -> Citation:
    """缓存中的引用 JSON → Citation（过滤未知字段，schema 演进向后兼容）。"""
    data = {k: v for k, v in payload.items() if k in Citation.model_fields}
    return Citation(**data)


_workflow: Optional[ResearchWorkflow] = None


def get_workflow(settings: Optional[Settings] = None) -> ResearchWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = ResearchWorkflow(settings)
    return _workflow

def _session_scope(task):
    import hashlib
    from ..security.access import get_access_store
    from ..security.accounts import get_account_store
    account=get_account_store().by_id(task.user_id)
    revision=str(get_access_store().epoch())+":"+str(account["version"] if account else 0)
    return hashlib.sha256((task.org_tag + "\0" + task.user_id + "\0" + revision).encode()).hexdigest()
