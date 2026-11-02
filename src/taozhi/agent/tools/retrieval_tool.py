"""受控知识检索工具：作为 Agent 核心层与 RAG 能力层之间的唯一桥梁。"""
from __future__ import annotations

from typing import Any, Dict, List

from ...observability.tracer import get_current_trace
from ...rag.retrieval.context_builder import ContextBuilder
from ...rag.retrieval.multi_retriever import MultiRetriever
from ...schemas.rag import RetrievalCandidate, RetrievalFilter, RetrievalRequest
from .base import BaseTool, ToolContext, ToolResult


class KnowledgeRetrievalTool(BaseTool):
    """面向 Agent 调用的受控知识库检索工具。"""

    name: str = "retrieve_knowledge"
    description: str = "检索企业内部已授权的商品规格、兼容性规则、售后政策与运营知识库。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词或查询句"},
            "top_k": {"type": "integer", "description": "返回候选切片上限", "default": 5},
        },
        "required": ["query"],
    }

    def __init__(self, retriever: MultiRetriever | None = None) -> None:
        self.retriever = retriever or MultiRetriever()
        self.context_builder = ContextBuilder()

    def run(self, params: Dict[str, Any], context: ToolContext) -> ToolResult:
        query = str(params.get("query", "")).strip()
        top_k = int(params.get("top_k", 5))

        if not query:
            return ToolResult(
                success=False,
                output="检索查询词不能为空",
                actionable_hint="请提供具体的检索查询词，例如商品型号或售后规则。",
            )

        # 严格执行 Pre-Retrieval 权限元数据前置注入
        retrieval_filter = RetrievalFilter(
            tenant_id=context.tenant_id,
            max_security_level=context.security_level,
        )
        req = RetrievalRequest(
            query=query,
            filter=retrieval_filter,
            top_k=top_k,
        )

        trace = get_current_trace()
        span_ctx = trace.span(
            "agent.tool.retrieval",
            kind="tool",
            input_data={"tool": self.name, "query": query, "top_k": top_k},
        ) if trace else None

        if span_ctx:
            with span_ctx as s:
                candidates = self.retriever.retrieve(req)
                s.output_data = {"returned_count": len(candidates)}
        else:
            candidates = self.retriever.retrieve(req)

        if not candidates:
            return ToolResult(
                success=True,
                output={"candidates": [], "context_text": "", "count": 0},
                actionable_hint="当前关键词未命中知识库资料，建议改写核心实体或提取同义词后重新检索。",
            )

        context_text, citations = self.context_builder.build_context(candidates)
        return ToolResult(
            success=True,
            output={
                "candidates": [c.model_dump() for c in candidates],
                "context_text": context_text,
                "citations": [c.model_dump() for c in citations],
                "count": len(candidates),
            },
            actionable_hint=f"成功检索到 {len(candidates)} 条高相关资料，请在回答中包含事实锚点 [doc_id:chunk_id]。",
        )