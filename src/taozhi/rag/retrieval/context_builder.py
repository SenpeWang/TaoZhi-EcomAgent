"""上下文构建器：将检索候选切片格式化为模型可消费的带溯源引用上下文。"""
from __future__ import annotations

from typing import List, Tuple
from ...schemas.rag import CitationItem, DocumentChunk, RetrievalCandidate


class ContextBuilder:
    """构建带标准引用锚点 [doc_id:chunk_id] 的上下文，并提取关联引用。"""

    def __init__(self, max_context_chars: int = 4000) -> None:
        self.max_context_chars = max_context_chars

    def build_context(
        self, candidates: List[RetrievalCandidate]
    ) -> Tuple[str, List[CitationItem]]:
        """将候选切片转化为 Prompt 上下文段落与 CitationItem 列表。"""
        if not candidates:
            return "", []

        context_parts: List[str] = []
        citations: List[CitationItem] = []
        total_chars = 0

        for idx, candidate in enumerate(candidates, 1):
            chunk: DocumentChunk = candidate.chunk
            anchor = f"[{chunk.doc_id}:{chunk.chunk_id}]"
            title_header = f"【资料 {idx}】来源: 《{chunk.doc_title}》 锚点: {anchor}"
            if chunk.line_start is not None and chunk.line_end is not None:
                title_header += f" (行 {chunk.line_start}-{chunk.line_end})"

            body = chunk.content.strip()
            block = f"{title_header}\n{body}\n"

            if total_chars + len(block) > self.max_context_chars and context_parts:
                break

            context_parts.append(block)
            total_chars += len(block)

            citations.append(
                CitationItem(
                    citation_id=f"cit_{idx}",
                    doc_id=chunk.doc_id,
                    doc_title=chunk.doc_title,
                    chunk_id=chunk.chunk_id,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    snippet=body[:200],
                )
            )

        full_context = "\n".join(context_parts)
        return full_context, citations

    def filter_citations_used_in_text(
        self, answer: str, citations: List[CitationItem]
    ) -> List[CitationItem]:
        """从模型最终答案中提取实际被引用到的 CitationItem。"""
        used_citations: List[CitationItem] = []
        for c in citations:
            anchor = f"[{c.doc_id}:{c.chunk_id}]"
            short_anchor = f"[{c.chunk_id}]"
            if anchor in answer or short_anchor in answer or c.doc_title in answer:
                used_citations.append(c)
        return used_citations if used_citations else citations