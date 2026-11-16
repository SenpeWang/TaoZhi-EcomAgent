"""企业语料文本抽取。

与 knowledge.ingest 共用底层 PDF/DOCX 解析实现，保证评测语料抽取与线上
附件入库口径一致，消除“测试用 A、生产用 B”导致的假绿差异。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .ingest import extract_docx_text_bytes, extract_pdf_pages_bytes

logger = logging.getLogger(__name__)

_PDF_SUFFIXES = {".pdf"}
_DOCX_SUFFIXES = {".docx"}


def read_corpus_text(path: Path) -> str:
    """读取文档文本：md/txt 直接读取；pdf/docx 复用 ingest 解析器抽取，全部失败才留空。"""
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        try:
            pages = extract_pdf_pages_bytes(path.read_bytes(), enforce_limits=False)
            return "\n".join(text for _, text in pages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PyMuPDF 抽取失败，尝试 pypdf: %s (%s)", path, exc)
        try:
            from pypdf import PdfReader  # noqa: PLC0415

            return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 文本抽取失败，已跳过内容: %s (%s)", path, exc)
            return ""
    if suffix in _DOCX_SUFFIXES:
        try:
            return extract_docx_text_bytes(path.read_bytes(), table_sep=" ")
        except Exception as exc:  # noqa: BLE001
            logger.warning("DOCX 文本抽取失败，已跳过内容: %s (%s)", path, exc)
            return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("文档读取失败，已跳过: %s (%s)", path, exc)
        return ""
