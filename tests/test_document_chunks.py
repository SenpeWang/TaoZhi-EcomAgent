"""验证知识文档「切片与词嵌入」视图的权限、脱敏与向量摘要。"""
import numpy as np
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


def _document() -> dict:
    """返回用于切片测试的已发布文档字典。"""
    return {"id": "doc_1", "title": "doc_1", "node_id": "team", "level": 1,
            "current_version": 2, "state": "published"}


def test_chunks_are_redacted_and_vectors_summarized(monkeypatch):
    """非老板视角切片文本脱敏，并返回维度、范数与前若干维预览。"""
    rows = [
        {
            "id": "chunk_a",
            "ordinal": 0,
            "page": 3,
            "text": "联系电话 13800138000 的保修条款",
            "embedding": np.array([0.5, 0.5, 0.5, 0.5], dtype="float32"),
        },
        {"id": "chunk_b", "ordinal": 1, "page": 4, "text": "尚未向量化", "embedding": None},
    ]
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: _document())
    monkeypatch.setattr(service, "require_document_read", lambda _c, _p, _d: None)
    monkeypatch.setattr(
        service.document_repo,
        "list_version_chunks_with_vectors",
        lambda _c, _doc_id, _version: rows,
    )
    monkeypatch.setattr(service, "model_signature", lambda: "stub:4")

    result = service.get_document_chunks(_principal(boss=False), "doc_1")

    assert result["version"] == 2
    assert result["total"] == 2
    assert result["embedded"] == 1
    assert result["dim"] == 4
    assert result["model"] == "stub:4"
    first, second = result["items"]
    assert first["text"] == "联系电话 138****8000 的保修条款"
    assert first["chars"] == len(rows[0]["text"])
    assert first["embedded"] is True
    assert first["norm"] == 1.0
    assert first["preview"] == [0.5, 0.5, 0.5, 0.5]
    assert second["embedded"] is False
    assert second["preview"] == []
    assert second["norm"] == 0.0


def test_boss_reads_unredacted_chunk_text(monkeypatch):
    """老板视角保留切片原文，便于与向量逐段核对。"""
    rows = [
        {
            "id": "chunk_a",
            "ordinal": 0,
            "page": 1,
            "text": "联系电话 13800138000",
            "embedding": np.array([1.0, 0.0], dtype="float32"),
        }
    ]
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: _document())
    monkeypatch.setattr(service, "require_document_read", lambda _c, _p, _d: None)
    monkeypatch.setattr(
        service.document_repo,
        "list_version_chunks_with_vectors",
        lambda _c, _doc_id, _version: rows,
    )
    monkeypatch.setattr(service, "model_signature", lambda: "stub:2")

    result = service.get_document_chunks(_principal(boss=True), "doc_1")

    assert result["items"][0]["text"] == "联系电话 13800138000"


def test_chunks_require_read_permission(monkeypatch):
    """无阅读权限时不得返回切片或向量。"""
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: _document())

    def _denied(*_args):
        """模拟权限拒绝的服务桩，统一抛出 404 拒绝异常。

        Args:
            _args: 被忽略的位置参数。
        """
        raise Denied("没有阅读权限", "NOT_FOUND", 404)

    monkeypatch.setattr(service, "require_document_read", _denied)

    with pytest.raises(Denied):
        service.get_document_chunks(_principal(boss=False), "doc_1")


def test_original_preview_requires_read_permission(monkeypatch):
    """无文档阅读权限时不得内联查看原件。"""
    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: _document())

    def _denied(*_args):
        """模拟下载阶段的权限拒绝服务桩。

        Args:
            _args: 被忽略的位置参数。
        """
        raise Denied("没有阅读权限", "NOT_FOUND", 404)

    monkeypatch.setattr(service, "require_document_read", _denied)

    with pytest.raises(Denied) as error:
        service.prepare_original_preview(_principal(boss=False), "doc_1")

    assert error.value.status == 404


def test_original_preview_streams_blob_for_reader(monkeypatch):
    """具备阅读权限的非老板成员可读取归档原件，并保留原始 MIME 与扩展名。"""
    class _Store:
        """文档服务测试用的对象存储桩，返回固定 PDF 字节。"""

        def open_blob(self, _tenant_id, _blob_name):
            """返回固定的 PDF 字节以替代真实对象存储。

            Args:
                _tenant_id: 被忽略的租户标识。
                _blob_name: 被忽略的对象名。
            """
            return b"%PDF-1.4 demo"

    monkeypatch.setattr(service, "database_connection", lambda: _Connection())
    monkeypatch.setattr(service, "get_document", lambda _c, _p, _doc_id: _document())
    monkeypatch.setattr(service, "require_document_read", lambda _c, _p, _d: None)
    monkeypatch.setattr(
        service.document_repo,
        "get_document_version",
        lambda _c, _doc_id, _version: {"blob_name": "blob_demo.pdf", "mime_type": "application/pdf"},
    )
    monkeypatch.setattr(service, "get_blob_store", lambda: _Store())
    monkeypatch.setattr(service, "audit", lambda *_args, **_kwargs: None)

    spec = service.prepare_original_preview(_principal(boss=False), "doc_1")

    assert spec["media_type"] == "application/pdf"
    assert spec["data"] == b"%PDF-1.4 demo"
    assert spec["filename"] == "doc_1.pdf"
