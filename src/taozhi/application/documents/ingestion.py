"""按任务租约解析授权原件、保存版本切片并提交发布审批。"""
from __future__ import annotations

from typing import Any

from taozhi.application.common.task_guard import validate_task_execution
from taozhi.domain.errors import Denied
from taozhi.infrastructure.knowledge.index_cache import encode_index
from taozhi.infrastructure.knowledge.ingest import parse, split
from taozhi.infrastructure.knowledge.storage import get_blob_store
from taozhi.infrastructure.persistence import admin_repo, document_repo, job_repo
from taozhi.infrastructure.persistence.access_repo import can_write, get_document
from taozhi.infrastructure.persistence.database import database_connection


def process_ingestion_job(job: dict[str, Any]) -> None:
    """执行文档解析、语义切片、向量编码与发布审批流提交。"""
    state = {"job_id": job["id"], "run_version": job["run_version"]}
    p, _ = validate_task_execution(state, "解析资料与登记来源", 35)
    with database_connection() as c:
        d = get_document(c, p, job["payload"]["document_id"])
        if not can_write(c, p, d) or d["current_version"] != job["payload"]["version"]:
            raise Denied("资料版本或写权限已变化", "STALE", 409)
        v = document_repo.get_document_version(c, d["id"], d["current_version"])
    if v["blob_name"]:
        body, pages = parse(get_blob_store().open_blob(p.tenant_id, v["blob_name"]), v["mime_type"])
    else:
        body, pages = v["body"], [(0, v["body"])]
    chunks = split(pages, d["id"], d["current_version"])
    with database_connection() as c:
        vectors = encode_index(c, p.tenant_id, [(d["title"], ch["text"]) for ch in chunks])
    validate_task_execution(state, "建立版本索引与发布申请", 75)
    with database_connection() as c:
        locked = job_repo.get_job_by_id(c, job["id"], for_update=True)
        d = get_document(c, p, d["id"])
        if (
            not locked
            or locked["run_version"] != job["run_version"]
            or locked["state"] != "running"
            or d["current_version"] != v["version"]
        ):
            raise Denied("执行版本已失效", "STALE", 409)
        document_repo.replace_version_chunks_and_mentions(
            c,
            tenant_id=p.tenant_id,
            doc_id=d["id"],
            version=v["version"],
            body=body,
            chunks=chunks,
            vectors=vectors,
        )
        document_repo.mark_document_review(c, d["id"])
        required = "boss" if d["level"] == 3 or d["scope"] == "company" else "leader"
        admin_repo.create_approval(
            c,
            p,
            "publish",
            d["id"],
            dict(expected_acl=d["acl_version"], expected_version=v["version"]),
            d["node_id"],
            required,
        )
        job_repo.complete_job(
            c,
            tenant_id=p.tenant_id,
            job_id=job["id"],
            run_version=job["run_version"],
            state="completed",
            stage="解析完成，等待发布审核",
        )
