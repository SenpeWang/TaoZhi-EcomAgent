"""按章节切块：为四路召回提供高质量检索单元（PRD B1）。"""

from __future__ import annotations

import re
from typing import List

from ..schemas.common import SourceDocument, TextChunk

_CHUNK_SIZE = 480
_CHUNK_OVERLAP = 90


def _split_long(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    if len(text) <= size:
        return [text]
    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        # 优先在句读处断句
        boundary = max(
            text.rfind("。", start, end),
            text.rfind("；", start, end),
            text.rfind("\n", start, end),
        )
        if boundary > start + size * 0.5:
            end = boundary + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


def chunk_document(doc: SourceDocument, chunk_size: int = _CHUNK_SIZE) -> List[TextChunk]:
    """先按章节切，再按长度滑窗；保留 page / section 用于引用溯源。"""
    chunks: List[TextChunk] = []
    text = doc.raw_text or ""

    # 有分页信息时优先按页切分，保证页码可溯源
    if doc.pages:
        for page in doc.pages:
            page_text = (page.get("text") or "").strip()
            if not page_text:
                continue
            for piece in _split_long(page_text, chunk_size):
                chunks.append(
                    TextChunk(
                        doc_id=doc.id,
                        doc_name=doc.title,
                        source=doc.source,
                        url=doc.url,
                        doc_type=doc.doc_type,
                        section=_guess_section(piece),
                        page=page.get("page"),
                        text=piece,
                        order=len(chunks),
                        owner_id=doc.owner_id,
                        org_tag=doc.org_tag,
                        is_public=doc.is_public,
                        meta={"synthetic": bool(doc.meta.get("synthetic"))},
                    )
                )
        return chunks

    for section, content in _iter_sections(text):
        for piece in _split_long(content, chunk_size):
            chunks.append(
                TextChunk(
                    doc_id=doc.id,
                    doc_name=doc.title,
                    source=doc.source,
                    url=doc.url,
                    doc_type=doc.doc_type,
                    section=section,
                    page=_guess_page(piece),
                    text=piece,
                    order=len(chunks),
                    owner_id=doc.owner_id,
                    org_tag=doc.org_tag,
                    is_public=doc.is_public,
                    meta={"synthetic": bool(doc.meta.get("synthetic"))},
                )
            )
    if not chunks and text:
        for piece in _split_long(text, chunk_size):
            chunks.append(
                TextChunk(doc_id=doc.id, doc_name=doc.title, source=doc.source,
                          url=doc.url, doc_type=doc.doc_type, text=piece, order=len(chunks))
            )
    return chunks


_HEADING_RE = re.compile(r"^\s*(#{1,4}\s+\S+|第[一二三四五六七八九十]+[章节]|[0-9]+(?:\.[0-9]+)*\s+\S)")


def _iter_sections(text: str):
    from .pdf import split_sections  # 局部导入避免循环

    return split_sections(text)


def _guess_section(piece: str) -> str:
    first = piece.strip().split("\n", 1)[0][:40]
    return first if _HEADING_RE.match(first) else ""


def _guess_page(piece: str) -> int | None:
    match = re.search(r"[Pp](\d{1,4})", piece[:200])
    return int(match.group(1)) if match else None


def chunk_documents(docs: List[SourceDocument], chunk_size: int = _CHUNK_SIZE) -> List[TextChunk]:
    result: List[TextChunk] = []
    for doc in docs:
        result.extend(chunk_document(doc, chunk_size))
    return result
