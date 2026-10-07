"""多模态文档解析：PDF 文本 / 跨页表格 / 图片抽取（PRD 模块 B）。"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_settings
from ..schemas.common import DocType, SourceDocument

_HEADING_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+[章节篇]|[0-9]+(?:\.[0-9]+)*\s+\S|[\(（][0-9一二三四五六七八九十]+[\)）]|[一二三四五六七八九十]+[、．.])\s*\S*"
)


def read_pdf(path: Path) -> Dict[str, Any]:
    """PyMuPDF 抽取：分页文本 + 表格 + 图片 base64。"""
    import pymupdf  # noqa: PLC0415

    doc = pymupdf.open(str(path))
    pages: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    images: List[str] = []
    try:
        for index, page in enumerate(doc):
            text = page.get_text("text") or ""
            has_image = bool(page.get_images(full=True))
            if has_image and len(images) < 6:
                try:
                    pix = page.get_pixmap(dpi=110)
                    images.append(base64.b64encode(pix.tobytes("png")).decode("utf-8"))
                except Exception:  # noqa: BLE001
                    pass
            try:
                found = page.find_tables()
                for table in found.tables:
                    rows = table.extract()
                    if rows and len(rows) > 1:
                        tables.append({"page": index + 1, "rows": rows})
            except Exception:  # noqa: BLE001
                pass
            pages.append({"page": index + 1, "text": text, "has_image": has_image})
    finally:
        doc.close()
    return {"pages": pages, "tables": tables, "images": images, "text": "\n".join(p["text"] for p in pages)}


def jina_read(url: str, api_key: str = "", timeout: int = 60) -> str:
    """Jina Reader：把网页 / PDF 链接直接解析为 Markdown。"""
    import httpx  # noqa: PLC0415

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = httpx.get(f"https://r.jina.ai/{url}", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception:  # noqa: BLE001
        pass
    return ""


def split_sections(text: str) -> List[Tuple[str, str]]:
    """按章节标题切分，返回 [(section, content)]。"""
    lines = text.split("\n")
    sections: List[Tuple[str, str]] = []
    current_title = "正文"
    buffer: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) <= 60 and _HEADING_RE.match(stripped) and not stripped.endswith("。"):
            if buffer:
                sections.append((current_title, "\n".join(buffer).strip()))
                buffer = []
            current_title = stripped
        else:
            buffer.append(line)
    if buffer:
        sections.append((current_title, "\n".join(buffer).strip()))
    return [(t, c) for t, c in sections if c]


def parse_pdf_file(path: Path, doc_type: DocType = DocType.MANUAL,
                   title: str = "") -> SourceDocument:
    payload = read_pdf(path)
    return SourceDocument(
        title=title or path.stem,
        source="pdf_upload",
        doc_type=doc_type,
        raw_text=payload["text"],
        pages=payload["pages"],
        tables=payload["tables"],
        images=payload["images"],
        url=f"file://{path}",
        meta={"sections": [s for s, _ in split_sections(payload["text"])]},
    )


def table_to_markdown(rows: List[List[Any]], max_rows: int = 12) -> str:
    rows = [[("" if c is None else str(c)).replace("\n", " ").strip() for c in row] for row in rows]
    rows = [r for r in rows if any(r)][:max_rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, *body = rows
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _merge_cross_page_tables(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跨页表格合并（PRD B3 的轻量实现：同表头跨页续接）。"""
    merged: List[Dict[str, Any]] = []
    for table in tables:
        rows = table.get("rows") or []
        if not rows:
            continue
        header = tuple(str(c).strip() for c in rows[0])
        if merged and merged[-1].get("header") == header:
            merged[-1]["rows"].extend(rows[1:])
        else:
            merged.append({"header": header, "page": table.get("page"), "rows": list(rows)})
    return merged


def tables_to_text(tables: List[Dict[str, Any]], limit: int = 5) -> str:
    merged = _merge_cross_page_tables(tables)[:limit]
    blocks = []
    for item in merged:
        md = table_to_markdown(item["rows"])
        if md:
            blocks.append(f"（P{item.get('page')} 表格）\n{md}")
    return "\n\n".join(blocks)


def extract_annual_report_facts(text: str) -> Dict[str, Any]:
    """企业年报四维关键信息规则抽取：供应商 / 客户 / 技术 / 产能（PRD B4）。"""
    patterns = {
        "suppliers": r"(?:前[五五1-5]大)?供应商[^\n]{0,40}?([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,20}(?:有限公司|股份|集团|科技))",
        "customers": r"(?:前[五五1-5]大)?客户[^\n]{0,40}?([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,20}(?:有限公司|股份|集团|科技))",
        "technologies": r"([\u4e00-\u9fa5]{2,12}(?:电池|材料|工艺|技术|平台|系统))",
        "capacity": r"(产能[^\n]{0,60}|年产能[^\n]{0,40}|产量[^\n]{0,40})",
    }
    result: Dict[str, Any] = {}
    for key, pattern in patterns.items():
        hits = re.findall(pattern, text)
        uniq: List[str] = []
        for hit in hits:
            value = hit if isinstance(hit, str) else " ".join(hit)
            value = value.strip()
            if value and value not in uniq:
                uniq.append(value)
        result[key] = uniq[:12]
    return result
