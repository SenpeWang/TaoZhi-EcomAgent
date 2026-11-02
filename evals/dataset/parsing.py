#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析层夹具：把规格渲染成真实 PDF/DOCX，再用入库同款解析器读回。

为什么写读用同一套库：被测对象是"入库解析链路"，不是第三方渲染器。渲染只需产出
真实文件（表格、页眉页脚、多页），读回必须走 ingestion 的 _read_corpus_text——
PDF/DOCX 路径一旦回归（例如 pypdf 缺失导致静默返回空串），解析层用例会直接失败，
而不是变成"文档入库了但永远检索不到"。

产物不落 git：只在生成阶段落到临时目录，manifest 记解析后的正文哈希。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

CJK = "china-s"   # PyMuPDF 内置简体中文字体，无需外部 TTF


def render_pdf(path: Path, title: str, rows, header: str = "", footer: str = "",
               pages: int = 1) -> None:
    import pymupdf

    doc = pymupdf.open()
    for page_no in range(pages):
        page = doc.new_page()
        if header:
            page.insert_text((60, 40), header, fontname=CJK, fontsize=9)
        y = 80
        page.insert_text((60, y), title, fontname=CJK, fontsize=16)
        y += 28
        for label, value in rows:
            page.insert_text((60, y), f"{label}：{value}", fontname=CJK, fontsize=11)
            y += 20
            rect = pymupdf.Rect(56, y - 16, 480, y - 2)
            page.draw_rect(rect, color=(0.6, 0.6, 0.6), width=0.5)
        if footer:
            page.insert_text((60, 800), footer, fontname=CJK, fontsize=8)
    doc.save(str(path))
    doc.close()


def render_docx(path: Path, title: str, rows, header: str = "", footer: str = "",
                pages: int = 1) -> None:
    from docx import Document

    d = Document()
    section = d.sections[0]
    if header:
        section.header.paragraphs[0].text = header
    if footer:
        section.footer.paragraphs[0].text = footer
    d.add_heading(title, level=1)
    table = d.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value
    if footer:
        d.add_paragraph(footer)
    d.save(str(path))


def build(entry: dict, out_dir: Path) -> tuple[Path, str]:
    """渲染一条解析夹具并读回文本，返回 (文件路径, 解析后正文)。"""
    fmt = entry["format"]
    suffix = ".pdf" if fmt == "pdf" else ".docx"
    path = out_dir / f"{entry['doc_id']}{suffix}"
    rows = [(r[0], r[1]) for r in entry["rows"]]
    args = (path, entry["title"], rows, entry.get("header", ""), entry.get("footer", ""),
            int(entry.get("pages", 1)))
    (render_pdf if fmt == "pdf" else render_docx)(*args)

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from taozhi.knowledge.corpus import read_corpus_text

    return path, read_corpus_text(path)


def build_all(entries) -> tuple[dict, list]:
    """批量渲染，返回 {doc_id: (path, text)} 与解析告警（正文为空或丢事实）。"""
    out = {}
    problems = []
    with tempfile.TemporaryDirectory(prefix="eval-parsing-") as tmp:
        tmp_dir = Path(tmp)
        for entry in entries:
            path, text = build(entry, tmp_dir)
            out[entry["doc_id"]] = (path, text)
            missing = [f for f in entry.get("facts", []) if f not in text]
            if not text.strip():
                problems.append(f"{entry['doc_id']}: 解析结果为空（{entry['format']}）")
            elif missing:
                problems.append(f"{entry['doc_id']}: 解析后丢失事实 {missing}")
    return out, problems
