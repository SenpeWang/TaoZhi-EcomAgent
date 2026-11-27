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
    """构造老板或普通员工的访问主体。

    Args:
        boss: 是否为老板身份。
    """
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
    """构造单页 PDF 字节，供文档解析测试使用。"""
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


def test_pdf_page_requires_read_permission(monkeypatch):
    """无文档阅读权限时不得通过翻页图片查看原件。"""
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: {"id": "doc_1"})

    def _denied(*_args):
        """模拟权限拒绝的服务桩，统一抛出 404 拒绝异常。

        Args:
            _args: 被忽略的位置参数。
        """
        raise Denied("没有阅读权限", "NOT_FOUND", 404)

    monkeypatch.setattr(service, "require_document_read", _denied)

    with pytest.raises(Denied) as error:
        service.document_pdf_page_count(_principal(boss=False), "doc_1")

    assert error.value.status == 404


def test_pdf_page_available_to_reader(monkeypatch):
    """具备阅读权限的非老板成员可查看原件页数与页面图。"""
    data = _pdf_bytes()
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(
        service, "get_document", lambda _c, _p, _doc_id: {"id": "doc_1", "current_version": 1}
    )
    monkeypatch.setattr(service, "require_document_read", lambda _c, _p, _d: None)
    monkeypatch.setattr(
        service.document_repo,
        "get_document_version",
        lambda _c, _doc_id, _version: {"blob_name": "blob.pdf", "mime_type": "application/pdf"},
    )

    class _Store:
        """PDF 服务测试用的对象存储桩，返回预置字节。"""

        def open_blob(self, _tenant_id, _name):
            """返回预置的 PDF 字节以替代真实对象存储。

            Args:
                _tenant_id: 被忽略的租户标识。
                _name: 被忽略的对象名。
            """
            return data

    monkeypatch.setattr(service, "get_blob_store", lambda: _Store())

    assert service.document_pdf_page_count(_principal(boss=False), "doc_1") == {"total": 1}
    assert service.render_document_pdf_page(_principal(boss=False), "doc_1", 1)["total"] == 1
