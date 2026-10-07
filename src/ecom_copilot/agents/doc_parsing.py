"""② 文档解析 Agent：商品资料多模态解析 + 章节切块 + 入库索引。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ..observability import inc
from ..parsing.chunker import chunk_documents
from ..parsing.pdf import extract_annual_report_facts, tables_to_text
from ..schemas.common import DocType, SourceDocument
from .state import ResearchRuntime, ResearchState, append_trace


def doc_parsing_node(state: ResearchState, config: RunnableConfig) -> Dict[str, Any]:
    runtime: ResearchRuntime = config["configurable"]["runtime"]
    started = time.perf_counter()

    documents: List[SourceDocument] = state.get("documents", []) or []
    enriched: List[SourceDocument] = []
    for doc in documents:
        if doc.doc_type in (DocType.SPEC_SHEET, DocType.MANUAL, DocType.FAQ) and doc.raw_text:
            # 商品资料要点抽取：参数表 / 适配表 / FAQ 三类要点
            facts = extract_annual_report_facts(doc.raw_text)
            if any(facts.values()):
                doc.meta["product_facts"] = facts
        if doc.tables and not doc.tables_description:
            doc.tables_description = tables_to_text(doc.tables)
        enriched.append(doc)

    chunks = chunk_documents(enriched)
    if runtime.engine is not None and chunks:
        runtime.engine.index_chunks(chunks)

    table_docs = sum(1 for d in enriched if d.tables)
    synthetic = sum(1 for d in enriched if d.meta.get("synthetic"))
    elapsed = time.perf_counter() - started
    inc("agent.doc_parsing.chunks", len(chunks))
    append_trace(state, "doc_parsing",
                 f"{len(enriched)} 篇文档 → {len(chunks)} 个切片（含表格 {table_docs} 篇）",
                 elapsed)

    return {
        "documents": enriched,
        "chunks": list(state.get("chunks", [])) + chunks,
        "parse_summary": {
            "documents": len(enriched),
            "chunks": len(chunks),
            "with_tables": table_docs,
            "synthetic": synthetic,
        },
        "trace": state.get("trace", []),
    }
