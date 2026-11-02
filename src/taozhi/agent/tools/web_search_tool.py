"""外部网络搜索兜底工具：当内部知识库切片不足或改写超限时触发。"""
from __future__ import annotations

from typing import Any, Dict, List

from ...observability.tracer import get_current_trace
from ...schemas.rag import CitationItem, DocumentChunk, RetrievalCandidate
from .base import BaseTool, ToolContext, ToolResult


class WebSearchFallbackTool(BaseTool):
    """外部电商与网络搜索兜底工具。"""

    name: str = "web_search_fallback"
    description: str = "在内部知识库未收录或时效性不足时，检索全网电商、行业资讯与市场公开信息。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "外部搜索关键词"},
            "limit": {"type": "integer", "description": "返回条目上限", "default": 5},
        },
        "required": ["query"],
    }

    def run(self, params: Dict[str, Any], context: ToolContext) -> ToolResult:
        query = str(params.get("query", "")).strip()
        limit = int(params.get("limit", 5))

        if not query:
            return ToolResult(
                success=False,
                output="搜索关键词不能为空",
                actionable_hint="请提供要搜索的品类名称或市场关键词。",
            )

        trace = get_current_trace()
        span_ctx = trace.span(
            "agent.tool.web_search",
            kind="tool",
            input_data={"tool": self.name, "query": query, "limit": limit},
        ) if trace else None

        raw_results: List[Dict[str, Any]] = []
        try:
            if span_ctx:
                with span_ctx as s:
                    from ...core.tools.search_web import search_web
                    raw_results = search_web(query, limit=limit)
                    s.output_data = {"returned_count": len(raw_results)}
            else:
                from ...core.tools.search_web import search_web
                raw_results = search_web(query, limit=limit)
        except Exception as exc:
            # 外部网络不可达时提供安全降级
            raw_results = []

        if not raw_results:
            return ToolResult(
                success=True,
                output={"candidates": [], "context_text": "", "count": 0},
                actionable_hint="全网搜索未返回有效资讯，建议检查关键词拼写或网络可用性。",
            )

        candidates: List[RetrievalCandidate] = []
        citations: List[CitationItem] = []
        context_parts: List[str] = []

        for idx, item in enumerate(raw_results[:limit], 1):
            title = item.get("title") or f"全网资讯 {idx}"
            snippet = item.get("snippet") or item.get("body") or ""
            url = item.get("url") or item.get("href") or "https://search.fallback"

            chunk = DocumentChunk(
                chunk_id=f"web_{idx}",
                doc_id=f"ext_{idx}",
                doc_title=title,
                content=snippet,
                security_level=0,
                metadata={"url": url, "source": "web_search"},
            )
            cand = RetrievalCandidate(chunk=chunk, score=0.70, channel="web_search")
            candidates.append(cand)

            anchor = f"[{chunk.doc_id}:{chunk.chunk_id}]"
            context_parts.append(f"【外部来源 {idx}】《{title}》 锚点: {anchor}\n{snippet}\n")
            citations.append(
                CitationItem(
                    citation_id=f"cit_web_{idx}",
                    doc_id=chunk.doc_id,
                    doc_title=title,
                    chunk_id=chunk.chunk_id,
                    snippet=snippet[:180],
                )
            )

        return ToolResult(
            success=True,
            output={
                "candidates": [c.model_dump() for c in candidates],
                "context_text": "\n".join(context_parts),
                "citations": [c.model_dump() for c in citations],
                "count": len(candidates),
            },
            actionable_hint="外部搜索已召回事实，请在回答中客观注明来源于全网公开信息。",
        )