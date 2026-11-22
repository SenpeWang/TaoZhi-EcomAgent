"""PDF 文本层与 OCR 回退的行为测试。"""
from __future__ import annotations

import fitz

from taozhi.infrastructure.knowledge import ingest


def _text_pdf() -> bytes:
    """生成一份带文本层的单页 PDF。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Screen protector installation guide with warranty terms.")
    data = doc.tobytes()
    doc.close()
    return data


def _blank_pdf() -> bytes:
    """生成一份只有图形、没有文本层的单页 PDF。"""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(80, 80, 260, 160), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    data = doc.tobytes()
    doc.close()
    return data


def test_text_pdf_skips_ocr(monkeypatch):
    """文本层充足时不触发 OCR。"""
    called = {"n": 0}
    monkeypatch.setattr(ingest, "_ocr_pdf_pages", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    pages = ingest.extract_pdf_pages_bytes(_text_pdf())
    assert pages and "Screen protector" in pages[0][1]
    assert called["n"] == 0


def test_blank_pdf_falls_back_to_ocr(monkeypatch):
    """文本层不足时调用 OCR 并采用识别结果。"""
    monkeypatch.setattr(ingest, "_ocr_available", lambda: True)
    monkeypatch.setattr(ingest, "_ocr_pdf_pages", lambda data, pages: [(1, "OCR 识别文本 " * 20)])
    result = ingest.extract_pdf_pages_bytes(_blank_pdf())
    assert result == [(1, "OCR 识别文本 " * 20)]


def test_ocr_disabled_by_env(monkeypatch):
    """显式关闭 OCR 时保留原始文本层结果。"""
    monkeypatch.setenv("ECOM_OCR_ENABLED", "false")
    assert ingest._ocr_available() is False
    result = ingest.extract_pdf_pages_bytes(_blank_pdf())
    assert result and result[0][1].strip() == ""