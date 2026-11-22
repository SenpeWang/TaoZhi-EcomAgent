"""文档、版本、切片、授权与实体提及数据库仓储。"""
from __future__ import annotations

import hashlib
from typing import Any

from taozhi.domain.access import evaluate_read_permission
from taozhi.domain.entities import Principal
from taozhi.domain.entity_extraction import extract_entities
from taozhi.infrastructure.persistence.database import as_jsonb, fetch_all, fetch_one


_DOCUMENTS_WITH_USER_GRANT = (
    "SELECT d.*,to_jsonb(g) AS user_grant FROM documents d "
    "LEFT JOIN document_grants g ON g.tenant_id=d.tenant_id AND g.document_id=d.id AND g.user_id=%s "
    "AND (g.expires_at IS NULL OR g.expires_at>now()) WHERE d.tenant_id=%s"
)


def list_active_documents(conn: Any, p: Principal, title_query: str) -> list[dict]:
    """筛选标题包含查询文本的未删除资料，同时取回该用户的有效授权。"""
    query = _DOCUMENTS_WITH_USER_GRANT + " AND d.state<>'deleted'"
    params = (p.id, p.tenant_id)
    if title_query:
        query += " AND strpos(lower(d.title), lower(%s)) > 0"
        params += (title_query,)
    return fetch_all(conn, query + " ORDER BY d.updated_at DESC, d.id LIMIT 2000", params)


def readable_published_documents(conn: Any, p: Principal, doc_id_prefix: str | None = None) -> dict[str, dict]:
    """一次查询载入已发布资料及有效授权，按原样匹配编号前缀并仅返回可读资料。"""
    query = _DOCUMENTS_WITH_USER_GRANT + " AND d.state='published'"
    params = (p.id, p.tenant_id)
    if doc_id_prefix:
        query += " AND starts_with(d.id,%s)"
        params += (doc_id_prefix,)
    return {
        doc["id"]: doc
        for doc in fetch_all(conn, query, params)
        if evaluate_read_permission(p, doc, doc["user_grant"])
    }


def get_published_document(conn: Any, tenant_id: str, doc_id: str) -> dict | None:
    """读取已发布文档记录。"""
    return fetch_one(
        conn,
        "SELECT * FROM documents WHERE tenant_id=%s AND id=%s AND state='published'",
        (tenant_id, doc_id),
    )


def find_document_any(conn: Any, tenant_id: str, doc_id: str) -> dict | None:
    """按编号读取任意状态文档（用于审批列表展示标题）。"""
    return fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, doc_id))


def lock_document(conn: Any, doc_id: str) -> dict | None:
    """锁定文档主记录等待同事务更新。"""
    return fetch_one(conn, "SELECT * FROM documents WHERE id=%s FOR UPDATE", (doc_id,))


def get_document_version(conn: Any, doc_id: str, version: int) -> dict | None:
    """读取指定文档版本记录。"""
    return fetch_one(
        conn,
        "SELECT * FROM document_versions WHERE document_id=%s AND version=%s",
        (doc_id, version),
    )


def list_document_versions(conn: Any, tenant_id: str, doc_id: str) -> list[dict]:
    """按版本号降序列出文档历史版本。"""
    return fetch_all(
        conn,
        "SELECT * FROM document_versions WHERE tenant_id=%s AND document_id=%s ORDER BY version DESC",
        (tenant_id, doc_id),
    )


def insert_document_draft(
    conn: Any,
    *,
    doc_id: str,
    tenant_id: str,
    node_id: str,
    creator_id: str,
    title: str,
    body: str,
    scope: str,
    level: int,
    blob: str | None,
    mime: str,
) -> None:
    """插入新文档主表记录及第 1 版草稿正文。"""
    conn.execute(
        "INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,download_allowed,state) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'ingesting')",
        (doc_id, tenant_id, node_id, creator_id, title, scope, level, level < 3, level == 1),
    )
    conn.execute(
        "INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,blob_name,mime_type,created_by,policy) "
        "VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s)",
        (
            tenant_id,
            doc_id,
            title,
            body,
            hashlib.sha256(body.encode()).hexdigest(),
            blob,
            mime,
            creator_id,
            as_jsonb(dict(node_id=node_id, scope=scope, level=level)),
        ),
    )


def insert_document_revision(
    conn: Any,
    *,
    tenant_id: str,
    doc_id: str,
    version: int,
    title: str,
    body: str,
    actor_id: str,
    policy: dict,
) -> None:
    """追加新修订版本并将文档切回解析中状态。"""
    conn.execute(
        "INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,created_by,policy) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant_id, doc_id, version, title, body, hashlib.sha256(body.encode()).hexdigest(), actor_id, as_jsonb(policy)),
    )
    conn.execute(
        "UPDATE documents SET title=%s,current_version=%s,state='ingesting',acl_version=acl_version+1,updated_at=now() WHERE id=%s",
        (title, version, doc_id),
    )


def mark_document_deleted(conn: Any, doc_id: str) -> None:
    """逻辑删除文档并递增权限版本号。"""
    conn.execute("UPDATE documents SET state='deleted',acl_version=acl_version+1,updated_at=now() WHERE id=%s", (doc_id,))


def mark_document_review(conn: Any, doc_id: str) -> None:
    """文档切片入库后推进至待审核发布状态。"""
    conn.execute("UPDATE documents SET state='review',error='' WHERE id=%s", (doc_id,))


def mark_document_ingest_failed(conn: Any, doc_id: str, version: int, message: str) -> None:
    """记录文档当前版本解析失败状态。"""
    conn.execute(
        "UPDATE documents SET state='failed',error=%s WHERE id=%s AND current_version=%s",
        (message, doc_id, version),
    )


def publish_document_state(conn: Any, doc_id: str) -> None:
    """将待审文档更新为已发布并递增 ACL 版本。"""
    conn.execute(
        "UPDATE documents SET state='published',acl_version=acl_version+1,updated_at=now() WHERE id=%s",
        (doc_id,),
    )


def update_document_policy_fields(conn: Any, doc_id: str, fields: dict[str, Any]) -> None:
    """更新文档密级/范围等策略字段并递增 ACL 版本。"""
    allowed_fields = ("scope", "node_id", "level", "ai_allowed", "download_allowed")
    for field in allowed_fields:
        if field in fields:
            conn.execute("UPDATE documents SET " + field + "=%s WHERE id=%s", (fields[field], doc_id))
    conn.execute("UPDATE documents SET acl_version=acl_version+1,updated_at=now() WHERE id=%s", (doc_id,))


def bump_document_acl(conn: Any, doc_id: str) -> None:
    """仅递增文档 ACL 版本号。"""
    conn.execute("UPDATE documents SET acl_version=acl_version+1 WHERE id=%s", (doc_id,))


def list_document_grants(conn: Any, tenant_id: str, doc_id: str) -> list[dict]:
    """查询文档显式个人授权明细。"""
    return fetch_all(
        conn,
        "SELECT g.user_id,u.display_name,g.effect,g.can_download,g.can_write,g.expires_at "
        "FROM document_grants g JOIN users u ON u.id=g.user_id WHERE g.tenant_id=%s AND g.document_id=%s",
        (tenant_id, doc_id),
    )


def upsert_or_revoke_grant(
    conn: Any,
    *,
    tenant_id: str,
    doc_id: str,
    target_user_id: str,
    granted_by: str,
    payload: dict,
) -> None:
    """写入、更新或吊销单文档特例授权。"""
    if payload.get("revoke"):
        conn.execute("DELETE FROM document_grants WHERE document_id=%s AND user_id=%s", (doc_id, target_user_id))
    else:
        conn.execute(
            "INSERT INTO document_grants(tenant_id,document_id,user_id,effect,can_read,can_download,can_write,expires_at,granted_by) "
            "VALUES(%s,%s,%s,%s,true,%s,%s,%s,%s) "
            "ON CONFLICT(document_id,user_id) DO UPDATE SET effect=EXCLUDED.effect,can_read=true,"
            "can_download=EXCLUDED.can_download,can_write=EXCLUDED.can_write,expires_at=EXCLUDED.expires_at,granted_by=EXCLUDED.granted_by",
            (
                tenant_id,
                doc_id,
                target_user_id,
                payload.get("effect", "allow"),
                payload.get("can_download", False),
                False,
                payload.get("expires_at"),
                granted_by,
            ),
        )
    bump_document_acl(conn, doc_id)


def has_version_chunks(conn: Any, doc_id: str, version: int) -> bool:
    """判断文档当前版本是否已完成切片写入。"""
    return bool(fetch_one(conn, "SELECT id FROM chunks WHERE document_id=%s AND version=%s", (doc_id, version)))


def list_version_chunks_for_index(conn: Any, doc_id: str, version: int) -> list[dict]:
    """读取待写入全文索引的切片及所属标题。"""
    return fetch_all(
        conn,
        "SELECT c.id,c.tenant_id,c.document_id,c.version,c.ordinal,c.page,c.text,d.title "
        "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.document_id=%s AND c.version=%s",
        (doc_id, version),
    )


def replace_version_chunks_and_mentions(
    conn: Any,
    *,
    tenant_id: str,
    doc_id: str,
    version: int,
    body: str,
    chunks: list[dict],
    vectors: Any,
) -> None:
    """更新解析正文并幂等替换指定版本的切片与商品实体提及。"""
    conn.execute(
        "UPDATE document_versions SET body=%s,content_hash=%s WHERE document_id=%s AND version=%s",
        (body, hashlib.sha256(body.encode()).hexdigest(), doc_id, version),
    )
    conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s AND version=%s", (doc_id, version))
    conn.execute("DELETE FROM chunks WHERE document_id=%s AND version=%s", (doc_id, version))
    for i, ch in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (ch["id"], tenant_id, doc_id, version, ch["ordinal"], ch["page"], ch["text"], None if vectors is None else vectors[i]),
        )
    index_mentions(conn, tenant_id, doc_id, version, chunks)


def index_mentions(conn: Any, tenant: str, doc_id: str, version: int, chunks: list[dict]) -> None:
    """抽取文档切片中的实体提及并幂等写入 knowledge_mentions。"""
    conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s AND version=%s", (doc_id, version))
    for chunk in chunks:
        for name in extract_entities(chunk["text"]):
            fid = "fact_" + hashlib.sha256((chunk["id"] + ":" + name).encode()).hexdigest()[:24]
            conn.execute(
                "INSERT INTO knowledge_mentions(id,tenant_id,document_id,version,chunk_id,entity) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (fid, tenant, doc_id, version, chunk["id"], name),
            )


def list_visible_mentions(conn: Any, p: Any) -> list[dict]:
    """列出当前用户有权访问且处于发布版本中的实体提及。"""
    allowed = readable_published_documents(conn, p)
    if not allowed:
        return []
    rows = fetch_all(
        conn,
        "SELECT f.* FROM knowledge_mentions f "
        "JOIN documents d ON d.id=f.document_id AND d.current_version=f.version "
        "WHERE f.tenant_id=%s AND f.document_id=ANY(%s) LIMIT 1000",
        (p.tenant_id, list(allowed)),
    )
    return [
        dict(
            entity=r["entity"],
            relation="资料提及商品",
            document_id=r["document_id"],
            version=r["version"],
            title=allowed[r["document_id"]]["title"],
        )
        for r in rows
    ]
