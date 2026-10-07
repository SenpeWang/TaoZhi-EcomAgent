"""① 资料采集 Agent：商品知识多源并行抓取（spawn_agent / wait_agent）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ..config import get_settings
from ..schemas.common import PermissionContext
from ..ingestion.base import SourceAdapter
from ..ingestion.sources import default_adapters
from ..observability import inc
from .state import ResearchRuntime, ResearchState, append_trace
from .subagents import SubAgentPool, SubAgentSpec


def data_collection_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    settings = runtime.settings or get_settings()
    task = state["task"]
    plan = state.get("plan")
    started = time.perf_counter()

    adapters: List[SourceAdapter] = default_adapters(settings)
    if plan and plan.target_sources:
        preferred = {a.name: a for a in adapters}
        ordered = [preferred[n] for n in plan.target_sources if n in preferred]
        # 兜底资料源永远保留（本地商品知识库语料 + 合成）
        ordered += [a for a in adapters if a.name in ("local_corpus", "synthetic")]
        adapters = list(dict.fromkeys(ordered))

    if settings.offline_mode:
        adapters = [a for a in adapters if a.name in ("local_corpus", "synthetic")]

    queries: List[str] = (plan.sub_questions if plan and plan.sub_questions else [task.question])
    queries = queries[: max(1, settings.max_parallel_subagents)]

    pool = SubAgentPool(max_parallel=settings.max_parallel_subagents)
    handles = []
    for query in queries:
        for adapter in adapters:
            handles.append(
                pool.spawn(
                    SubAgentSpec(
                        name=f"{adapter.name}:{query[:24]}",
                        query=query,
                        adapter=adapter,
                        limit=max(2, task.max_sources // max(1, len(adapters))),
                        permission={"user_id": task.user_id, "org_tag": task.org_tag},
                    )
                )
            )
    pool.wait(handles, timeout=240)
    permission = state.get("permission") or PermissionContext()
    documents = [d for d in pool.merge(handles)
                 if permission.visible(d.owner_id,d.org_tag,d.is_public,d.id)
                 and (settings.synthetic_fallback_enabled or d.source != "synthetic")]

    # 去重（与历史文档比对）
    known = {d.title for d in state.get("documents", [])}
    fresh = [d for d in documents if d.title not in known]

    elapsed = time.perf_counter() - started
    inc("agent.data_collection.docs", len(fresh))
    append_trace(
        state, "data_collection",
        f"spawn {len(handles)} 个子 Agent，命中 {len(fresh)} 份商品资料",
        elapsed, subagent_report=pool.report(handles)[:12],
    )

    return {
        "documents": list(state.get("documents", [])) + fresh,
        "trace": state.get("trace", []),
        "metrics": {**state.get("metrics", {}),
                    "documents": len(state.get("documents", [])) + len(fresh),
                    "subagents": len(handles)},
    }
