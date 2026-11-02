"""接入层同步问答端点：集成语义缓存拦截与编排层闭环流转。"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..agent.tools.base import ToolContext
from ..agent.tools.retrieval_tool import KnowledgeRetrievalTool
from ..config import get_settings
from ..infrastructure.cache.semantic_cache import SemanticCache, default_semantic_cache
from ..observability.tracer import active_trace, get_current_trace
from ..orchestration.loop import AgenticRAGOrchestrator
from ..orchestration.state import OrchestrationState
from ..schemas.chat import ChatRequest, ChatResponse
from ..schemas.rag import CitationItem

router = APIRouter(prefix="/api/chat", tags=["chat"])


def run_chat_service(
    request: ChatRequest,
    tenant_id: str = "default",
    user_id: Optional[str] = None,
    security_level: int = 0,
    orchestrator: Optional[AgenticRAGOrchestrator] = None,
    cache: Optional[SemanticCache] = None,
    retrieval_tool: Optional[KnowledgeRetrievalTool] = None,
) -> ChatResponse:
    """运行接入层主逻辑：语义缓存拦截 -> 编排层 5 节点迭代状态机 -> 异步回写缓存。"""
    session_id = request.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    semantic_cache = cache or default_semantic_cache

    if orchestrator:
        runner = orchestrator
    elif retrieval_tool:
        runner = AgenticRAGOrchestrator(retrieval_tool=retrieval_tool)
    else:
        runner = AgenticRAGOrchestrator()

    with active_trace(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
    ) as trace:
        start_time = time.time()

        # 1. 语义缓存前置拦截：高频近邻重复意图直达
        cached_hit = semantic_cache.get(
            request.question, tenant_id=tenant_id, security_level=security_level
        )
        if cached_hit:
            duration_ms = round((time.time() - start_time) * 1000, 2)
            raw_cits = cached_hit.get("citations", [])
            trace_rec = trace.finish()
            return ChatResponse(
                answer=cached_hit["answer"],
                citations=[CitationItem(**c) for c in raw_cits],
                session_id=session_id,
                trace_id=trace_rec.trace_id,
                duration_ms=duration_ms,
                is_fallback=False,
                cached=True,
            )

        # 2. 编排层显式状态机流转 (Decide -> Retrieve -> Evaluate -> Rewrite -> Answer)
        init_state = OrchestrationState(
            session_id=session_id,
            trace_id=trace.context.trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            security_level=security_level,
            original_query=request.question,
            current_query=request.question,
        )

        final_state = runner.run(init_state)
        duration_ms = round((time.time() - start_time) * 1000, 2)
        trace_record = trace.finish()

        # 3. 正常终态异步写入语义缓存 (非降级分支)
        if not final_state.is_fallback and final_state.answer:
            semantic_cache.set(
                query=request.question,
                tenant_id=tenant_id,
                security_level=security_level,
                answer=final_state.answer,
                citations=[c.model_dump() for c in final_state.citations],
            )

        return ChatResponse(
            answer=final_state.answer,
            citations=final_state.citations,
            session_id=session_id,
            trace_id=trace_record.trace_id,
            duration_ms=duration_ms,
            is_fallback=final_state.is_fallback,
            cached=False,
        )


# 向后兼容别名
run_chat_minimal_loop = run_chat_service


@router.post("", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """同步问答接口（接入层只负责参数校验与响应封装）。"""
    return run_chat_service(request)