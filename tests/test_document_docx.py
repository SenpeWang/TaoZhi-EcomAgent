"""验证 DOCX 原件转 HTML 的版式保留与文本转义。"""
import io

from docx import Document

from taozhi.application.documents import service
from taozhi.infrastructure.knowledge.docx_html import render_docx_html


def _docx_bytes() -> bytes:
    """构造含页眉与标题的 DOCX 字节，供文档解析测试使用。"""
    document = Document()
    document.sections[0].header.paragraphs[0].text = "内部资料"
    document.add_heading("售后政策", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("加粗条款").bold = True
    document.add_paragraph("<script>alert(1)</script>")
    table = document.add_table(rows=0, cols=2)
    cells = table.add_row().cells
    cells[0].text = "退款时限"
    cells[1].text = "48 小时"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_html_keeps_layout_and_escapes_text():
    """标题、表格与页眉保留版式，正文中的脚本按文本转义。"""
    html = render_docx_html(_docx_bytes())

    assert "<h2>售后政策</h2>" in html
    assert "<table><tbody><tr><td>退款时限</td><td>48 小时</td></tr></tbody></table>" in html
    assert "内部资料" in html
    assert "<strong>加粗条款</strong>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_document_docx_uses_original_bytes(monkeypatch):
    """渲染入口直接消费原件字节，不重新回落到解析正文。"""
    monkeypatch.setattr(service, "_load_docx_original", lambda _p, _doc_id: _docx_bytes())

    result = service.render_document_docx(object(), "doc_1")

    assert "<h2>售后政策</h2>" in result["html"]
