"""文档入库校验、统一文本抽取与结构感知切片。"""
from __future__ import annotations

import importlib.util
import io
import os
import zipfile
from typing import List, Optional, Tuple

from taozhi.domain.errors import Denied
from taozhi.domain.ranking import split_document_chunks


def validate_upload(name: str, data: bytes) -> str:
    """校验上传附件大小、扩展名与文件魔数，返回 MIME 类型。"""
    if not data or len(data) > 20 * 1024 * 1024:
        raise Denied("附件大小须为 1 字节至 20 MB", "UPLOAD_SIZE", 400)
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        if not data.startswith(b"%PDF-"):
            raise Denied("PDF 格式无效", "UPLOAD_FORMAT", 400)
        return "application/pdf"
    if suffix == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if (
                    "word/document.xml" not in z.namelist()
                    or sum(x.file_size for x in z.infolist()) > 30 * 1024 * 1024
                    or any(x.filename.startswith("/") or ".." in x.filename.split("/") for x in z.infolist())
                ):
                    raise ValueError()
        except Exception:
            raise Denied("文档格式无效或解压内容过大", "UPLOAD_FORMAT", 400) from None
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    raise Denied("支持 PDF 和 Word 文档", "UPLOAD_FORMAT", 400)


OCR_PAGE_MIN_CHARS = 20
OCR_DPI = 150


def _ocr_available() -> bool:
    """判断当前环境是否启用并具备 OCR 能力。

    Returns:
        OCR 开关打开且识别库可导入时返回 True。
    """
    if os.environ.get("ECOM_OCR_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return False
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def _ocr_pdf_pages(data: bytes, pages: int) -> List[Tuple[int, str]]:
    """对无文本层的 PDF 逐页渲染并识别文字。

    Args:
        data: PDF 字节流。
        pages: 需要识别的页数上限。

    Returns:
        按页的识别文本；识别库不可用时返回空列表。
    """
    import fitz
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    results: List[Tuple[int, str]] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for index in range(min(pages, len(pdf))):
            pixmap = pdf[index].get_pixmap(dpi=OCR_DPI)
            outcome, _ = engine(pixmap.tobytes("png"))
            text = "\n".join(item[1] for item in (outcome or []))
            results.append((index + 1, text))
    return results


def extract_pdf_pages_bytes(data: bytes, enforce_limits: bool = True) -> List[Tuple[int, str]]:
    """按页提取 PDF 文本；文本层不足时回退到 OCR 识别。

    Args:
        data: PDF 字节流。
        enforce_limits: 是否限制加密与页数上限。

    Returns:
        [(1-based 页码, 文本), ...]。
    """
    import fitz

    with fitz.open(stream=data, filetype="pdf") as pdf:
        if enforce_limits and (pdf.is_encrypted or len(pdf) > 200):
            raise Denied("暂不支持加密或超过 200 页的 PDF", "PARSE_LIMIT", 400)
        pages = [(i + 1, pdf[i].get_text("text")) for i in range(len(pdf))]
    empty_pages = [number for number, text in pages if len(text.strip()) < OCR_PAGE_MIN_CHARS]
    if not empty_pages or not _ocr_available():
        return pages
    ocr_pages = {number: text for number, text in _ocr_pdf_pages(data, len(pages))}
    merged = [(number, ocr_pages.get(number, text) if number in empty_pages else text)
              for number, text in pages]
    if not any(text.strip() for _, text in merged):
        return pages
    return merged


def extract_docx_text_bytes(data: bytes, table_sep: str = " | ") -> str:
    """提取 DOCX 正文、页眉/页脚与表格文本。

    页眉/页脚常携带制度版本号、备案号与生效日期；与语料读取共用本实现，
    消除生产上传漏读页眉与评测测试走不同路径产生的假绿差异。
    """
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for section in doc.sections:
        parts.extend(p.text for p in section.header.paragraphs)
        parts.extend(p.text for p in section.footer.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            parts.append(table_sep.join(cell.text.strip() for cell in row.cells))
    return "\n".join(x for x in parts if x.strip())


def parse(data: bytes, mime: str) -> Tuple[str, List[Tuple[int, str]]]:
    """解析上传文档字节流为 (合并正文, 按页列表)。"""
    if mime == "application/pdf":
        pages = extract_pdf_pages_bytes(data, enforce_limits=True)
    elif "wordprocessingml" in mime:
        pages = [(0, extract_docx_text_bytes(data, table_sep=" | "))]
    else:
        raise Denied("不支持的文档格式", "UPLOAD_FORMAT", 400)
    body = "\n\n".join(text for _, text in pages)
    if not body.strip() or len(body) > 200000:
        raise Denied("未提取到可读文本或内容超过限制", "PARSE_LIMIT", 400)
    return body, pages


def _chunk_limits() -> Tuple[int, int]:
    """从运行时配置读取切片尺寸与重叠；单测无库环境回落 Config 类定义默认值。"""
    from taozhi.config import Config, load_config

    try:
        cfg = load_config()
        return max(128, int(cfg.chunk_size)), max(0, int(cfg.chunk_overlap))
    except Exception:
        return int(Config.chunk_size), int(Config.chunk_overlap)


def split(
    pages: List[Tuple[int, str]],
    doc_id: str,
    version: int,
    max_chars: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[dict]:
    """读取切片配置并将结构化切片工作委托给领域内核 split_document_chunks。"""
    default_size, default_overlap = _chunk_limits()
    limit = max(128, max_chars if max_chars is not None else default_size)
    overlap_budget = max(0, min(limit // 2, overlap if overlap is not None else default_overlap))
    return split_document_chunks(pages, doc_id, version, max_chars=limit, overlap=overlap_budget)
