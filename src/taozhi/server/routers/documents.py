"""企业资料查询、上传解析、版本修订、策略变更与授权下载路由。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError

from taozhi.application.documents import service
from taozhi.domain.errors import Denied
from taozhi.schemas.document import Doc, DocPolicy, EditDoc, Grant
from taozhi.server.auth import identity

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
def documents(p=Depends(identity), q: str = Query("", max_length=200, pattern=r"^[^\x00]*$")):
    """按标题查询当前用户有权阅读的文档。"""
    return {"items": service.list_documents(p, title_query=q.strip())}


@router.post("", status_code=201)
def add_document(payload: Doc, p=Depends(identity)):
    """创建文本草稿资料并提交解析入库任务。"""
    return service.create_document_draft(p, **payload.model_dump())


@router.post("/upload", status_code=201)
async def upload(
    file: UploadFile = File(...),
    title: str = Form(...),
    node_id: str = Form(...),
    scope: str = Form("org"),
    level: int = Form(2),
    p=Depends(identity),
):
    """接收 PDF/DOCX/TXT 附件上传并入队解析索引。"""
    content = await file.read(20 * 1024 * 1024 + 1)
    try:
        parsed = Doc(title=title, body="待解析附件", node_id=node_id, scope=scope, level=level)
    except ValidationError:
        raise Denied("资料名称、范围或密级格式不正确", "INVALID_INPUT", 422) from None
    return service.upload_document_blob(
        p,
        filename=file.filename or "",
        content=content,
        title=parsed.title,
        node_id=parsed.node_id,
        scope=parsed.scope,
        level=parsed.level,
    )


@router.get("/{doc_id}")
def get_doc(doc_id: str, p=Depends(identity)):
    """读取文档详情与按主体脱敏后的正文。"""
    return service.get_document_detail(p, doc_id)


@router.get("/{doc_id}/versions")
def versions(doc_id: str, p=Depends(identity)):
    """读取用户有权查阅的文档历史版本记录。"""
    return {"items": service.list_document_versions(p, doc_id)}


@router.get("/{doc_id}/chunks")
def chunks(doc_id: str, p=Depends(identity)):
    """读取文档当前版本的切片与词嵌入摘要，供原文/向量对照查看。"""
    return service.get_document_chunks(p, doc_id)


@router.put("/{doc_id}")
def edit(doc_id: str, payload: EditDoc, p=Depends(identity)):
    """提交新修订版本并重新触发切片解析与发布审批。"""
    return service.edit_document(p, doc_id, **payload.model_dump())


@router.delete("/{doc_id}")
def remove(doc_id: str, p=Depends(identity)):
    """逻辑删除资料并立即撤回检索可见性。"""
    return service.delete_document(p, doc_id)


@router.post("/{doc_id}/policy", status_code=202)
def policy_change(doc_id: str, payload: DocPolicy, p=Depends(identity)):
    """提交文档密级、可见范围与 AI/下载策略调整申请。"""
    return service.request_policy_change(p, doc_id, payload.model_dump(exclude_none=True))


@router.get("/{doc_id}/permission-status")
def permission_status(doc_id: str, p=Depends(identity)):
    """系统管理员/老板查询文档实时权限状态。"""
    return service.get_permission_status(p, doc_id)


@router.get("/{doc_id}/grants")
def grants(doc_id: str, p=Depends(identity)):
    """查看单文档个人特例授权列表。"""
    return {"items": service.list_grants(p, doc_id)}


@router.post("/{doc_id}/grants", status_code=202)
def grant_change(doc_id: str, payload: Grant, p=Depends(identity)):
    """发起单文档个人特例授权或撤销审批。"""
    return service.request_grant_change(
        p,
        doc_id,
        user_id=payload.user_id,
        expires_at=payload.expires_at,
        payload_json=payload.model_dump(mode="json"),
    )


@router.post("/{doc_id}/request-access", status_code=202)
def request_access(doc_id: str, p=Depends(identity)):
    """员工自助发起已发布文档阅读权限申请。"""
    return service.request_document_access(p, doc_id)


@router.get("/{doc_id}/original")
def original(doc_id: str, p=Depends(identity)):
    """老板在浏览器内联查看归档原件；其他身份与无原件资料按统一错误返回。"""
    spec = service.prepare_original_preview(p, doc_id)
    return Response(
        spec["data"],
        media_type=spec["media_type"],
        headers={"Content-Disposition": "inline; filename*=UTF-8''" + quote(spec["filename"])},
    )


@router.get("/{doc_id}/download")
def download(doc_id: str, p=Depends(identity)):
    """受控下载脱敏文本或（老板视角）预签名/代理直出原生附件文件。"""
    spec = service.prepare_download(p, doc_id)
    if spec["kind"] == "redirect":
        return RedirectResponse(spec["url"], status_code=302)
    if spec["kind"] == "blob":
        return Response(
            spec["data"],
            media_type=spec["media_type"],
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(spec["filename"])},
        )
    if spec["kind"] == "file":
        return FileResponse(spec["path"], media_type=spec["media_type"], filename=spec["filename"])
    return Response(
        spec["content"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(spec["filename"])},
    )
