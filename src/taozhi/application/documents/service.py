"""资料上传、版本编辑、权限策略与知识地图应用服务。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taozhi.config import load_config
from taozhi.domain.access import evaluate_download_permission, evaluate_read_permission, evaluate_write_permission
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.domain.roles import is_system_admin
from taozhi.infrastructure.persistence import admin_repo, document_repo, job_repo
from taozhi.infrastructure.persistence.access_repo import (
    audit,
    can_download,
    can_write,
    get_document,
    require_document_read,
    require_system,
)
from taozhi.infrastructure.knowledge import es_index
from taozhi.infrastructure.knowledge.embedding import model_signature, to_vector
from taozhi.infrastructure.knowledge.docx_html import render_docx_html
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.infrastructure.knowledge.ingest import validate_upload
from taozhi.infrastructure.knowledge.semantic_cache import get_semantic_cache
from taozhi.infrastructure.knowledge.storage import get_blob_store

_LOG = logging.getLogger(__name__)

_MIME_EXT_MAP = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}


def list_documents(p: Principal, *, title_query: str = "") -> list[dict[str, Any]]:
    """列出当前用户有权阅读的未删除资料清单。"""
    with database_connection() as c:
        rows = document_repo.list_active_documents(c, p, title_query)
        result = []
        for document in rows:
            grant = document["user_grant"]
            if not evaluate_read_permission(p, document, grant, include_draft=True):
                continue
            item = {
                key: document[key]
                for key in (
                    "id", "title", "scope", "node_id", "level", "state", "current_version",
                    "acl_version", "ai_allowed", "download_allowed", "updated_at", "error",
                )
            }
            item.update(
                node_name=p.nodes.get(document["node_id"], {}).get("name", ""),
                can_write=evaluate_write_permission(p, document, grant),
                can_download=evaluate_download_permission(p, document, grant),
                can_policy=p.boss or is_system_admin(p.role_level) or p.manages(document["node_id"]),
            )
            result.append(item)
        return result


def create_document_draft(
    p: Principal,
    *,
    title: str,
    body: str,
    node_id: str,
    scope: str,
    level: int,
    blob: str | None = None,
    mime: str = "text/plain",
) -> dict[str, str]:
    """创建资料第 1 版草稿并异步入队解析任务。"""
    if node_id not in p.nodes:
        raise Denied("组织不存在", "INVALID", 400)
    if scope == "company" and node_id != p.root:
        raise Denied("公司共享资料必须归属公司", "INVALID", 400)
    allowed = (
        p.boss
        or (p.manages(node_id) and scope != "company" and level <= 2)
        or (is_system_admin(p.role_level) and scope == "company" and level == 1)
    )
    if not allowed:
        raise Denied("不能在该范围创建资料")
    did = job_repo.generate_id("doc")
    with database_connection() as c:
        document_repo.insert_document_draft(
            c,
            doc_id=did,
            tenant_id=p.tenant_id,
            node_id=node_id,
            creator_id=p.id,
            title=title,
            body=body,
            scope=scope,
            level=level,
            blob=blob,
            mime=mime,
        )
        job = job_repo.create_job(c, p, "ingest", node_id, dict(document_id=did, version=1), level=level)
        audit(c, p, "创建资料草稿", did, node_id, level)
    return dict(id=did, task_id=job["id"], message="资料正在解析，审核发布后才可见")


def upload_document_blob(
    p: Principal,
    *,
    filename: str,
    content: bytes,
    title: str,
    node_id: str,
    scope: str,
    level: int,
) -> dict[str, str]:
    """校验上传附件、写入对象存储并触发文档解析入库。"""
    mime = validate_upload(filename, content)
    blob_name = f"{job_repo.generate_id('blob')}.{_MIME_EXT_MAP[mime]}"
    store = get_blob_store()
    store.put_blob(p.tenant_id, blob_name, content)
    try:
        return create_document_draft(
            p,
            title=title,
            body="待解析附件",
            node_id=node_id,
            scope=scope,
            level=level,
            blob=blob_name,
            mime=mime,
        )
    except Exception:
        store.remove_blob(p.tenant_id, blob_name)
        raise


def get_document_detail(p: Principal, doc_id: str) -> dict[str, Any]:
    """读取资料详情与当前版本正文（非老板视角执行敏感字段脱敏）。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        grant = require_document_read(c, p, d)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
        redacted_body = redact(v["body"])
        return dict(
            **{
                k: d[k]
                for k in (
                    "id",
                    "title",
                    "scope",
                    "node_id",
                    "level",
                    "state",
                    "current_version",
                    "acl_version",
                    "ai_allowed",
                    "download_allowed",
                    "error",
                )
            },
            body=v["body"] if p.boss else redacted_body,
            can_write=evaluate_write_permission(p, d, grant) and (p.boss or v["body"] == redacted_body),
            can_download=evaluate_download_permission(p, d, grant),
            mime_type=v["mime_type"],
            has_original=bool(v["blob_name"]),
        )


_VECTOR_PREVIEW_DIMS = 24


def get_document_chunks(p: Principal, doc_id: str) -> dict[str, Any]:
    """读取文档当前版本的切片、页码与词嵌入摘要，供原文/向量对照查看。

    切片文本与正文遵循同一脱敏规则；向量对具备阅读权限的成员可见，
    只返回维度、L2 范数和前若干维预览，避免整表 512 维数值撑大响应。
    """
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        require_document_read(c, p, d)
        rows = document_repo.list_version_chunks_with_vectors(c, d["id"], d["current_version"])
    items: list[dict[str, Any]] = []
    dim = 0
    for row in rows:
        vector = to_vector(row.get("embedding"))
        has_vector = vector is not None and len(vector) > 0
        if has_vector:
            dim = len(vector)
        items.append(
            dict(
                id=row["id"],
                ordinal=row["ordinal"],
                page=row["page"],
                chars=len(row["text"]),
                text=row["text"] if p.boss else redact(row["text"]),
                embedded=has_vector,
                dim=len(vector) if has_vector else 0,
                norm=round(sum(float(v) * float(v) for v in vector) ** 0.5, 4) if has_vector else 0.0,
                preview=(
                    [round(float(v), 4) for v in vector[:_VECTOR_PREVIEW_DIMS]] if has_vector else []
                ),
            )
        )
    embedded = sum(1 for item in items if item["embedded"])
    return dict(
        version=d["current_version"],
        state=d["state"],
        total=len(items),
        embedded=embedded,
        model=model_signature() if embedded else "",
        dim=dim,
        items=items,
    )


def list_document_versions(p: Principal, doc_id: str) -> list[dict[str, Any]]:
    """按历史策略快照权限过滤并返回资料版本历史。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        grant = require_document_read(c, p, d)
        result = []
        for v in document_repo.list_document_versions(c, p.tenant_id, d["id"]):
            historic = {**d, **v["policy"]}
            if v["version"] == d["current_version"] or evaluate_read_permission(p, historic, grant, include_draft=True):
                item = {k: v[k] for k in ("version", "title", "body", "created_at", "content_hash")}
                if not p.boss:
                    item["body"] = redact(item["body"])
                result.append(item)
        return result


def edit_document(p: Principal, doc_id: str, *, title: str, body: str, version: int) -> dict[str, str]:
    """基于乐观锁创建资料新修订版本、失效缓存并重新解析索引。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not can_write(c, p, d):
            raise Denied("没有资料写权限")
        locked = document_repo.lock_document(c, d["id"])
        original = document_repo.get_document_version(c, d["id"], locked["current_version"])["body"]
        if not p.boss and original != redact(original):
            raise Denied("资料包含敏感字段，原文编辑需要老板身份", "FIELD_WRITE_RESTRICTED", 403)
        if locked["current_version"] != version:
            raise Denied("资料已被修改，请刷新后重试", "VERSION_CONFLICT", 409)
        next_ver = version + 1
        document_repo.insert_document_revision(
            c,
            tenant_id=p.tenant_id,
            doc_id=d["id"],
            version=next_ver,
            title=title,
            body=body,
            actor_id=p.id,
            policy={k: d[k] for k in ("node_id", "scope", "level")},
        )
        admin_repo.invalidate_authorization(c, p.tenant_id)
        get_semantic_cache().invalidate(p.tenant_id, document_id=d["id"])
        job = job_repo.create_job(c, p, "ingest", d["node_id"], dict(document_id=d["id"], version=next_ver), level=d["level"])
        audit(c, p, "编辑资料版本", d["id"], d["node_id"], d["level"], detail={"version": next_ver})
    return {"message": "新版本正在解析，等待发布审核", "task_id": job["id"]}


def delete_document(p: Principal, doc_id: str) -> dict[str, str]:
    """逻辑删除资料并立即清理语义缓存与 ES 索引切片。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not can_write(c, p, d):
            raise Denied("没有资料删除权限")
        document_repo.mark_document_deleted(c, d["id"])
        admin_repo.invalidate_authorization(c, p.tenant_id)
        audit(c, p, "删除资料", d["id"], d["node_id"], d["level"])
        get_semantic_cache().invalidate(p.tenant_id, document_id=d["id"])
        try:
            if es_index.available():
                es_index.delete_chunks(d["id"])
        except Exception as exc:
            _LOG.warning("删除文档时同步清理 ES 切片失败 (%s): %s", d["id"], exc)
    return {"message": "资料已删除，历史来源不再可用"}


def request_policy_change(p: Principal, doc_id: str, data: dict[str, Any]) -> dict:
    """发起文档密级/归属/AI 与下载策略变更审批。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not (p.boss or is_system_admin(p.role_level) or p.manages(d["node_id"])):
            raise Denied()
        if data.get("node_id", d["node_id"]) not in p.nodes:
            raise Denied("组织无效", "INVALID", 400)
        scope = data.get("scope", d["scope"])
        node = data.get("node_id", d["node_id"])
        if scope == "company" and node != p.root:
            raise Denied("公司共享资料应归属公司", "INVALID", 400)
        return admin_repo.create_approval(
            c,
            p,
            "document_policy",
            d["id"],
            dict(**data, expected_acl=d["acl_version"], expected_version=d["current_version"]),
            d["node_id"],
        )


def get_permission_status(p: Principal, doc_id: str) -> dict[str, Any]:
    """系统管理员/老板查询文档实时权限与密级配置。"""
    require_system(p)
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        audit(c, p, "查询资料授权配置", d["id"], system=True)
        return {
            k: d[k]
            for k in ("id", "scope", "node_id", "level", "state", "current_version", "acl_version", "ai_allowed", "download_allowed")
        }


def list_grants(p: Principal, doc_id: str) -> list[dict[str, Any]]:
    """查询文档显式特例授权明细。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not (p.boss or is_system_admin(p.role_level) or p.manages(d["node_id"])):
            raise Denied()
        return document_repo.list_document_grants(c, p.tenant_id, d["id"])


def request_grant_change(
    p: Principal,
    doc_id: str,
    *,
    user_id: str,
    expires_at: datetime | None,
    payload_json: dict[str, Any],
) -> dict:
    """发起单文档显式授权或吊销审批申请。"""
    if expires_at and (expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc)):
        raise Denied("授权到期时间须为未来时间", "INVALID_EXPIRY", 400)
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not (p.boss or is_system_admin(p.role_level) or p.manages(d["node_id"])):
            raise Denied()
        if not admin_repo.get_user(c, p.tenant_id, user_id, active_only=True):
            raise Denied("授权人员无效", "INVALID", 400)
        return admin_repo.create_approval(
            c,
            p,
            "grant",
            d["id"],
            dict(**payload_json, expected_acl=d["acl_version"], expected_version=d["current_version"]),
            d["node_id"],
        )


def request_document_access(p: Principal, doc_id: str) -> dict[str, str]:
    """员工自助发起已发布文档阅读权限申请。"""
    with database_connection() as c:
        d = document_repo.get_published_document(c, p.tenant_id, doc_id)
        if d:
            admin_repo.create_approval(
                c,
                p,
                "grant",
                doc_id,
                dict(
                    user_id=p.id,
                    can_download=False,
                    can_write=False,
                    effect="allow",
                    expected_acl=d["acl_version"],
                    expected_version=d["current_version"],
                ),
                d["node_id"],
            )
    return {"message": "申请已提交，资料负责人确认后可开放访问"}


def prepare_download(p: Principal, doc_id: str) -> dict[str, Any]:
    """校验下载权限、记录下载审计并生成脱敏文本或原始附件下载描述。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        if not can_download(c, p, d):
            raise Denied("没有资料下载权限", "NOT_FOUND", 404)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
        audit(c, p, "下载资料", d["id"], d["node_id"], d["level"])
        if v["blob_name"]:
            if not p.boss:
                return {"kind": "text", "filename": f"{d['title']}.txt", "content": redact(v["body"])}
            cfg = load_config()
            store = get_blob_store()
            if cfg.minio_public_endpoint:
                url = store.presign_url(p.tenant_id, v["blob_name"])
                if url:
                    return {"kind": "redirect", "url": url}
            ext = Path(v["blob_name"]).suffix or f".{_MIME_EXT_MAP.get(v['mime_type'], 'bin')}"
            try:
                data = store.open_blob(p.tenant_id, v["blob_name"])
            except Exception as exc:
                raise Denied("附件不可用", "NOT_FOUND", 404) from exc
            return {
                "kind": "blob",
                "data": data,
                "media_type": v["mime_type"],
                "filename": d["title"] + ext,
            }
        return {
            "kind": "text",
            "filename": f"{d['title']}.txt",
            "content": v["body"] if p.boss else redact(v["body"]),
        }


def prepare_original_preview(p: Principal, doc_id: str) -> dict[str, Any]:
    """校验文档阅读权限后返回归档原件，供浏览器内联查看。

    原件不做脱敏，查看范围与原始文档的阅读权限一致，不额外区分身份。
    """
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        require_document_read(c, p, d)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
        if not v["blob_name"]:
            raise Denied("该资料没有归档原件", "NOT_FOUND", 404)
        try:
            data = get_blob_store().open_blob(p.tenant_id, v["blob_name"])
        except Exception as exc:
            raise Denied("原件不可用", "NOT_FOUND", 404) from exc
        audit(c, p, "预览资料原件", d["id"], d["node_id"], d["level"])
        return {
            "data": data,
            "media_type": v["mime_type"],
            "filename": d["title"] + (Path(v["blob_name"]).suffix or ".bin"),
        }


def _load_pdf_original(p: Principal, doc_id: str) -> bytes:
    """校验文档阅读权限后返回当前版本的 PDF 原件字节。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        require_document_read(c, p, d)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
        if not v["blob_name"] or v["mime_type"] != "application/pdf":
            raise Denied("该资料没有 PDF 原件", "NOT_FOUND", 404)
        try:
            return get_blob_store().open_blob(p.tenant_id, v["blob_name"])
        except Exception as exc:
            raise Denied("原件不可用", "NOT_FOUND", 404) from exc


def document_pdf_page_count(p: Principal, doc_id: str) -> dict[str, int]:
    """返回 PDF 原件的总页数，供前端翻页查看。"""
    import fitz

    data = _load_pdf_original(p, doc_id)
    with fitz.open(stream=data, filetype="pdf") as doc:
        return {"total": doc.page_count}


def render_document_pdf_page(p: Principal, doc_id: str, page_no: int) -> dict[str, Any]:
    """把 PDF 原件的指定页渲染为 PNG，避免依赖浏览器内置 PDF 插件。"""
    import fitz

    data = _load_pdf_original(p, doc_id)
    with fitz.open(stream=data, filetype="pdf") as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise Denied("页码超出范围", "INVALID", 400)
        pixmap = doc[page_no - 1].get_pixmap(dpi=144)
        return {"data": pixmap.tobytes("png"), "total": doc.page_count}


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _load_docx_original(p: Principal, doc_id: str) -> bytes:
    """校验文档阅读权限后返回当前版本的 DOCX 原件字节。"""
    with database_connection() as c:
        d = get_document(c, p, doc_id)
        require_document_read(c, p, d)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
        if not v["blob_name"] or v["mime_type"] != _DOCX_MIME:
            raise Denied("该资料没有 DOCX 原件", "NOT_FOUND", 404)
        try:
            return get_blob_store().open_blob(p.tenant_id, v["blob_name"])
        except Exception as exc:
            raise Denied("原件不可用", "NOT_FOUND", 404) from exc


def render_document_docx(p: Principal, doc_id: str) -> dict[str, str]:
    """把 DOCX 原件渲染为 HTML 片段，保留标题、段落与表格版式。"""
    return {"html": render_docx_html(_load_docx_original(p, doc_id))}


def list_knowledge_map(p: Principal) -> list[dict[str, Any]]:
    """读取当前用户可见的已发布文档实体关联图谱。"""
    with database_connection() as c:
        return document_repo.list_visible_mentions(c, p)
