"""DOCX 原件转 HTML 片段：保留标题层级、段落、列表与表格，文本全部转义。"""
from __future__ import annotations

import html
import io
from typing import Iterator

from docx import Document
from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph


def _iter_blocks(document: DocumentType) -> Iterator[Paragraph | Table]:
    """按文档正文顺序产出段落与表格，保持原始排版次序。"""
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def _paragraph_html(paragraph: Paragraph) -> str:
    """把段落转成标题、列表或正文 HTML，保留粗体、斜体与下划线。"""
    parts = []
    for run in paragraph.runs:
        piece = html.escape(run.text or "")
        if not piece:
            continue
        if run.bold:
            piece = f"<strong>{piece}</strong>"
        if run.italic:
            piece = f"<em>{piece}</em>"
        if run.underline:
            piece = f"<u>{piece}</u>"
        parts.append(piece)
    inner = "".join(parts) or html.escape(paragraph.text or "")
    if not inner.strip():
        return ""
    style = (paragraph.style.name or "").lower()
    if "heading" in style or "标题" in style:
        digits = [char for char in style if char.isdigit()]
        level = min(4, max(2, int(digits[0]) + 1)) if digits else 2
        return f"<h{level}>{inner}</h{level}>"
    if "list bullet" in style or "列表段落" in style:
        return f"<ul><li>{inner}</li></ul>"
    if "list number" in style:
        return f"<ol><li>{inner}</li></ol>"
    return f"<p>{inner}</p>"


def _table_html(table: Table) -> str:
    """把表格转成 HTML，合并单元格按原始文本重复呈现。"""
    rows = []
    for row in table.rows:
        cells = "".join(f"<td>{html.escape(cell.text)}</td>" for cell in row.cells)
        rows.append(f"<tr>{cells}</tr>")
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def render_docx_html(data: bytes) -> str:
    """把 DOCX 字节渲染为可安全插入页面的 HTML 片段。"""
    document = Document(io.BytesIO(data))
    parts: list[str] = []
    for section in document.sections:
        header = " ".join(item.text for item in section.header.paragraphs if item.text.strip())
        if header:
            parts.append(f'<div class="docx-meta">{html.escape(header)}</div>')
    for block in _iter_blocks(document):
        parts.append(_table_html(block) if isinstance(block, Table) else _paragraph_html(block))
    for section in document.sections:
        footer = " ".join(item.text for item in section.footer.paragraphs if item.text.strip())
        if footer:
            parts.append(f'<div class="docx-meta">{html.escape(footer)}</div>')
    return "\n".join(part for part in parts if part)
