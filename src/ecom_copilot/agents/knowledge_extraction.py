"""③ 知识抽取 Agent：商品 / SKU / 配件 / 兼容关系与故障三元组抽取。"""

from __future__ import annotations

import time
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from ..graph.extractor import KnowledgeExtractor
from ..observability import inc
from ..schemas.common import PermissionContext
from .state import ResearchRuntime, ResearchState, append_trace


def knowledge_extraction_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    task = state["task"]
    started = time.perf_counter()

    permission: PermissionContext = state.get("permission") or PermissionContext()
    chunks = list(state.get("chunks", []))

    # 用用户问题做一次语义召回，优先抽取最相关的商品资料切片（控制 token）
    selected = chunks
    if runtime.engine is not None and len(chunks) > 12:
        try:
            from ..retrieval.channels import RetrievalHit

            hits = runtime.engine.retrieve(task.question, top_k=12, permission=permission)
            hit_ids = {h.chunk.id for h in hits}
            prioritized = [c for c in chunks if c.id in hit_ids]
            selected = prioritized + [c for c in chunks if c.id not in hit_ids][:8]
        except Exception:  # noqa: BLE001
            selected = chunks[:20]

    extractor = KnowledgeExtractor(max_items=14)
    extraction = extractor.extract(selected[:20], question=task.question)

    # 证据入池，供后续交叉核验与答案生成引用
    evidence_pool = list(state.get("evidence_pool", []))
    seen = {e.quote[:60] for e in evidence_pool}
    for triple in extraction.triples:
        for ev in triple.evidence:
            if ev.quote[:60] not in seen:
                evidence_pool.append(ev)
                seen.add(ev.quote[:60])

    elapsed = time.perf_counter() - started
    inc("agent.extraction.entities", len(extraction.entities))
    append_trace(state, "knowledge_extraction",
                 f"抽取 {len(extraction.entities)} 个实体 / {len(extraction.triples)} 条关系",
                 elapsed)

    return {
        "extraction": extraction,
        "evidence_pool": evidence_pool[:200],
        "trace": state.get("trace", []),
        "metrics": {**state.get("metrics", {}),
                    "entities": len(extraction.entities),
                    "triples": len(extraction.triples)},
    }
