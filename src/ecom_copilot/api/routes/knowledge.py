"""文档目录、正文、上传、编辑、删除和独立授权，均以企业身份为边界。"""
from __future__ import annotations
import asyncio
from typing import Literal
from fastapi import APIRouter,Depends,File,Form,HTTPException,UploadFile
from pydantic import BaseModel,ConfigDict,Field
from ...retrieval import get_retrieval_engine
from ...parsing.chunker import chunk_documents
from ...schemas.common import DocType,SourceDocument
from ...security.auth import User,require_permission
from ...security.accounts import get_account_store
from ...security.access import get_access_store,VISIBILITIES
from ...ingestion.guard import detect_injection,sanitize,allowed_tools
router=APIRouter(prefix="/api/knowledge",tags=["企业文档"])
class SearchRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    query:str=Field(min_length=1,max_length=2000)
    top_k:int=Field(default=8,ge=1,le=30)
    channels:list[str]|None=None
class DocumentWrite(BaseModel):
    model_config=ConfigDict(extra="forbid")
    title:str=Field(min_length=1,max_length=200)
    text:str=Field(min_length=1,max_length=200000)
    doc_type:str="other"
    visibility:Literal["management","company","selected"]="management"
    allowed_users:list[str]=Field(default_factory=list,max_length=200)
class Permissions(BaseModel):
    model_config=ConfigDict(extra="forbid")
    visibility:Literal["management","company","selected"]
    allowed_users:list[str]=Field(default_factory=list,max_length=200)
class EditDocument(BaseModel):
    model_config=ConfigDict(extra="forbid")
    title:str|None=Field(default=None,min_length=1,max_length=200)
    text:str|None=Field(default=None,min_length=1,max_length=200000)
def _doc_type(v):
    names={"商品手册":"manual","参数表":"spec_sheet","详情页":"product_page","教程":"tutorial","售后FAQ":"faq","售后政策":"policy"}
    try:return DocType(names.get(v,v))
    except ValueError:return DocType.OTHER
def _grantees(ids,user):
    if len(ids)!=len(set(ids)):raise HTTPException(400,"授权人员重复")
    store=get_account_store()
    for uid in ids:
        target=store.by_id(uid)
        if not target or target["org_tag"]!=user.org_tag or not target["active"]:
            raise HTTPException(400,"只能授权给本企业的有效账号")
    return ids
def _record(doc_id,user,manage=False):
    record=get_access_store().get(doc_id)
    if not record or record["deleted"] or not get_access_store().allowed(record,user.context()):
        raise HTTPException(404,"文档不存在或无权访问")
    if manage and (record["org_tag"]!=user.org_tag and not user.is_admin):raise HTTPException(404,"文档不存在")
    return record
def _brief(record,user):
    return {k:v for k,v in record.items() if k!="allowed_users" or user.has("knowledge:grant")}
def _add(doc,user,visibility="management",allowed=None):
    store=get_access_store();doc.owner_id=user.user_id;doc.org_tag=user.org_tag;doc.is_public=False
    store.register(doc.id,doc.title,user.org_tag,user.user_id,visibility,allowed)
    try:added=get_retrieval_engine().index_documents([doc])
    except Exception:
        store.update(doc.id,user.org_tag,delete=True)
        raise
    get_account_store().audit(user.user_id,user.org_tag,"新增文档",doc.id,{"visibility":visibility})
    return {"doc_id":doc.id,"title":doc.title,"chunks":added,"visibility":visibility}

@router.get("/documents")
def documents(user:User=Depends(require_permission("knowledge:read"))):
    return {"documents":[_brief(d,user) for d in get_access_store().list(user.context())]}
@router.get("/documents/{doc_id}")
def document(doc_id:str,user:User=Depends(require_permission("knowledge:read"))):
    engine=get_retrieval_engine()
    with engine._lock:
        record=_record(doc_id,user)
        chunks=sorted([c for c in engine.index.chunks if c.doc_id==doc_id],key=lambda c:c.order)
        # Chunks may overlap; page labels are retained for reading.
        return {**_brief(record,user),"text":"\n\n".join(c.text for c in chunks),"chunks":len(chunks)}
@router.post("/documents",status_code=201)
def add_document(payload:DocumentWrite,user:User=Depends(require_permission("knowledge:write"))):
    if detect_injection(payload.text)[0]:raise HTTPException(400,"资料包含疑似指令注入，请检查正文")
    ids=_grantees(payload.allowed_users,user)
    if payload.visibility=="selected" and not ids:raise HTTPException(400,"指定人员可读时至少选择一人")
    return _add(SourceDocument(title=payload.title,raw_text=sanitize(payload.text,max_chars=200000),
                doc_type=_doc_type(payload.doc_type),source="企业录入"),user,payload.visibility,ids)
@router.patch("/documents/{doc_id}")
def edit_document(doc_id:str,payload:EditDocument,user:User=Depends(require_permission("knowledge:write"))):
    engine=get_retrieval_engine()
    if payload.text and detect_injection(payload.text)[0]:raise HTTPException(400,"资料包含疑似指令注入")
    with engine._lock:
        record=_record(doc_id,user,True)
        if payload.text is not None:
            doc=SourceDocument(id=doc_id,title=payload.title or record["title"],raw_text=sanitize(payload.text,max_chars=200000),
                org_tag=record["org_tag"],owner_id=record["owner_id"],is_public=False,source="企业录入")
            remaining=[c for c in engine.index.chunks if c.doc_id!=doc_id]
            engine.index.chunks=remaining+chunk_documents([doc]);engine.index._rebuild()
        elif payload.title is not None:
            for c in engine.index.chunks:
                if c.doc_id==doc_id:c.doc_name=payload.title
        updated=get_access_store().update(doc_id,user.org_tag,title=payload.title)
        engine.cache.clear();engine.index.save()
    get_account_store().audit(user.user_id,user.org_tag,"编辑文档",doc_id)
    return _brief(updated,user)
@router.patch("/documents/{doc_id}/permissions")
def permissions(doc_id:str,payload:Permissions,user:User=Depends(require_permission("knowledge:grant"))):
    ids=_grantees(payload.allowed_users,user)
    if payload.visibility=="selected" and not ids:raise HTTPException(400,"至少选择一位获授权人员")
    engine=get_retrieval_engine()
    with engine._lock:
        _record(doc_id,user,True)
        record=get_access_store().update(doc_id,user.org_tag,payload.visibility,ids)
        engine.cache.clear()
    get_account_store().audit(user.user_id,user.org_tag,"调整文档权限",doc_id,
                              {"visibility":payload.visibility,"allowed_users":ids})
    return _brief(record,user)
@router.delete("/documents/{doc_id}")
def delete_document(doc_id:str,user:User=Depends(require_permission("knowledge:write"))):
    engine=get_retrieval_engine()
    with engine._lock:
        _record(doc_id,user,True)
        get_access_store().update(doc_id,user.org_tag,delete=True)
        engine.index.chunks=[c for c in engine.index.chunks if c.doc_id!=doc_id]
        engine.index._rebuild();engine.cache.clear();engine.index.save()
    get_account_store().audit(user.user_id,user.org_tag,"删除文档",doc_id)
    return {"message":"文档已删除，旧问答中的相关正文访问也已关闭"}
@router.post("/upload")
async def upload(file:UploadFile=File(...),doc_type:str=Form("商品手册"),
                 visibility:str=Form("management"),allowed_users:str=Form("[]"),
                 user:User=Depends(require_permission("knowledge:write"))):
    from pathlib import Path
    from ...config import get_settings
    from ...parsing.pipeline import get_pipeline
    from functools import partial
    import json
    if visibility not in VISIBILITIES:raise HTTPException(400,"文档可见范围无效")
    try:ids=json.loads(allowed_users);assert isinstance(ids,list) and all(isinstance(x,str) for x in ids) and len(ids)<=200
    except (ValueError,AssertionError):raise HTTPException(400,"授权人员格式无效")
    _grantees(ids,user)
    if visibility=="selected" and not ids:raise HTTPException(400,"至少选择一位获授权人员")
    filename=file.filename or ""
    if "/" in filename or "\\" in filename or Path(filename).suffix.lower() not in (".pdf",".txt",".md",".docx"):
        raise HTTPException(400,"仅支持 PDF、TXT、Markdown、DOCX，文件名不能包含路径")
    data=await file.read(get_settings().max_upload_bytes+1)
    if len(data)>get_settings().max_upload_bytes:raise HTTPException(413,"文件超过 20 MB 限制")
    if not data:raise HTTPException(400,"文件为空")
    doc=await asyncio.to_thread(partial(get_pipeline().submit_bytes,data,filename,doc_type=_doc_type(doc_type)))
    if not doc.raw_text.strip():raise HTTPException(400,"未识别到文档正文，请检查文件内容")
    return await asyncio.to_thread(_add,doc,user,visibility,ids)
@router.post("/search")
def search(payload:SearchRequest,user:User=Depends(require_permission("knowledge:read"))):
    hits=get_retrieval_engine().retrieve(payload.query,top_k=payload.top_k,permission=user.context(),channels=payload.channels)
    return {"query":payload.query,"hits":[{"doc_id":h.chunk.doc_id,"doc_name":h.chunk.doc_name,"page":h.chunk.page,
            "text":h.chunk.text[:400],"rank":h.rank,"score":round(h.score,5)} for h in hits]}
@router.get("/diagnose")
def diagnose(query:str,user:User=Depends(require_permission("knowledge:read"))):
    return get_retrieval_engine().diagnose(query,user.context())
@router.get("/stats")
def stats(user:User=Depends(require_permission("knowledge:read"))):
    docs=get_access_store().list(user.context())
    return {"documents":len(docs),"scope":"仅当前账号获授权的企业文档"}
@router.get("/tools")
def tools(user:User=Depends(require_permission("knowledge:read"))):return {"tools":allowed_tools()}
@router.post("/ingest")
def ingest(payload:dict,user:User=Depends(require_permission("knowledge:write"))):
    # Online employees never trigger source adapters; imports are an explicit management action.
    from ...ingestion.sources import adapters_by_name
    from ...ingestion.base import FetchRequest
    names=payload.get("sources") or ["local_corpus"]
    if names!=["local_corpus"]:raise HTTPException(400,"当前仅支持管理员导入演示语料，其他资料请上传")
    request=FetchRequest(keywords=payload.get("keywords") or [],limit=min(30,int(payload.get("limit",10))),
                         permission={"user_id":user.user_id,"org_tag":user.org_tag})
    imported=[]
    for adapter in adapters_by_name(names):
        for doc in adapter.safe_fetch(request):
            imported.append(_add(doc,user,"management"))
    return {"documents":len(imported),"items":imported}
