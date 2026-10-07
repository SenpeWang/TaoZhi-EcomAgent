"""多模态文档解析层（PRD 模块 B）。"""

from .chunker import chunk_document, chunk_documents  # noqa: F401
from .pdf import (  # noqa: F401
    extract_annual_report_facts,
    jina_read,
    parse_pdf_file,
    read_pdf,
    split_sections,
    table_to_markdown,
    tables_to_text,
)
from .pipeline import DocumentParsingPipeline, ParseJob, get_pipeline  # noqa: F401
from .vision import VisionParser  # noqa: F401

__all__ = [
    "chunk_document", "chunk_documents",
    "extract_annual_report_facts", "jina_read", "parse_pdf_file", "read_pdf",
    "split_sections", "table_to_markdown", "tables_to_text",
    "DocumentParsingPipeline", "ParseJob", "get_pipeline", "VisionParser",
]
