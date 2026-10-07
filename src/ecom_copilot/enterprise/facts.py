"""原文中的商品提及关系；不推断未在原文出现的产品属性。"""
import re,hashlib
from .db import fetch_all
from .policy import can_read
def index_mentions(conn,tenant,doc_id,version,chunks):
    conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s AND version=%s",(doc_id,version))
    for chunk in chunks:
        for name in set(re.findall(r"(?<![A-Za-z0-9])[A-Za-z]{1,8}[-_]\d{2,6}(?![A-Za-z0-9])",chunk["text"])):
            fid="fact_"+hashlib.sha256((chunk["id"]+":"+name).encode()).hexdigest()[:24]
            conn.execute("INSERT INTO knowledge_mentions(id,tenant_id,document_id,version,chunk_id,entity) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(fid,tenant,doc_id,version,chunk["id"],name))
def list_visible_mentions(conn,p):
    docs=fetch_all(conn,"SELECT * FROM documents WHERE tenant_id=%s AND state='published'",(p.tenant_id,))
    allowed={d["id"]:d for d in docs if can_read(conn,p,d)}
    if not allowed:return []
    rows=fetch_all(conn,"SELECT f.* FROM knowledge_mentions f JOIN documents d ON d.id=f.document_id AND d.current_version=f.version WHERE f.tenant_id=%s AND f.document_id=ANY(%s) LIMIT 1000",(p.tenant_id,list(allowed)))
    return [dict(entity=r["entity"],relation="资料提及商品",document_id=r["document_id"],version=r["version"],title=allowed[r["document_id"]]["title"]) for r in rows]
