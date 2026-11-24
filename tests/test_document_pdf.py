"""验证 PDF 原件按页渲染的权限、页码边界与图片输出。"""
import pymupdf
import pytest

from taozhi.application.documents import service
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied


class _Connection:
    """替代真实数据库连接的最小上下文管理器。"""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _principal(boss: bool) -> Principal:
    memberships = [{"node_id": "org_root", "role": "boss"}] if boss else [{"node_id": "team", "role": "staff"}]
    return Principal(
        "user_1",
        "tenant_1",
        "member",
        "成员",
        1,
        1,
        False,
        memberships,
        {
            "org_root": {"id": "org_root", "kind": "company", "parent_id": None},
            "team": {"id": "team", "kind": "team", "parent_id": "org_root"},
        },
    )


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "PDF page")
    return document.tobytes()


def test_pdf_pages_render_to_png(monkeypatch):
    """总页数与指定页图片来自同一份原件，并返回 PNG 字节。"""
    data = _pdf_bytes()
    monkeypatch.setattr(service, "_load_pdf_original", lambda _p, _doc_id: data)

    assert service.document_pdf_page_count(_principal(boss=True), "doc_1") == {"total": 1}
    rendered = service.render_document_pdf_page(_principal(boss=True), "doc_1", 1)

    assert rendered["total"] == 1
    assert rendered["data"].startswith(b"\x89PNG")


def test_pdf_page_number_must_be_within_range(monkeypatch):
    """超出页码范围返回业务拒绝，不渲染空白图片。"""
    monkeypatch.setattr(service, "_load_pdf_original", lambda _p, _doc_id: _pdf_bytes())

    with pytest.raises(Denied) as error:
        service.render_document_pdf_page(_principal(boss=True), "doc_1", 2)

    assert error.value.code == "INVALID"


def test_pdf_page_requires_boss(monkeypatch):
    """非老板不得通过翻页图片绕过原件脱敏限制。"""
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: {"id": "doc_1"})
    monkeypatch.setattr(service, "can_download", lambda _c, _p, _d: True)

    with pytest.raises(Denied) as error:
        service.document_pdf_page_count(_principal(boss=False), "doc_1")

    assert error.value.status == 403
