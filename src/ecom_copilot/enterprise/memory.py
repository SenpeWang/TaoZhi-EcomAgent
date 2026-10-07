"""个人来源记忆：仅在追问时复用仍然有效的原文证据。"""
import re
from .db import fetch_all,fetch_one
from .policy import sources_valid
from .privacy import redact
def get_followup_context(conn,p,question,input_level):
    if not re.search(r"刚才|上面|这款|那个|它的|继续|价格呢|还有呢",question):return [],[]
    for past in fetch_all(conn,"SELECT result,sources,input_level FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='ask' AND state='completed' ORDER BY created_at DESC LIMIT 3",(p.tenant_id,p.id)):
        if not past["result"] or past["input_level"]>input_level or not sources_valid(conn,p,past["sources"]):continue
        evidence=[]
        for citation in past["result"].get("citations",[])[:5]:
            chunk=fetch_one(conn,"SELECT c.*,d.title,d.node_id,d.level,d.ai_allowed,d.acl_version FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.tenant_id=%s AND c.id=%s AND d.current_version=c.version AND d.state='published'",(p.tenant_id,citation["chunk_id"]))
            if not chunk:continue
            evidence.append(dict(document_id=chunk["document_id"],version=chunk["version"],acl_version=chunk["acl_version"],chunk_id=chunk["id"],
                title=chunk["title"],quote=chunk["text"] if p.boss else redact(chunk["text"]),page=chunk["page"],level=chunk["level"],
                node_id=chunk["node_id"],tenant_id=p.tenant_id,ai_allowed=chunk["ai_allowed"],score=1.0,kind="document"))
        if evidence:return evidence,past["sources"]
    return [],[]
