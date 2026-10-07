"""文档授权注册表。正文、检索证据及历史任务共用当前权限。"""
from __future__ import annotations
import json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from ..config import get_settings
VISIBILITIES={"management":"仅管理员和老板","company":"全体员工可读","selected":"指定人员可读"}

class AccessStore:
    def __init__(self,path=None):
        self.path=Path(path or get_settings().data_dir/"state"/"document-access.sqlite")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,title TEXT,org_tag TEXT NOT NULL,owner_id TEXT,
visibility TEXT NOT NULL,allowed_users TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,
updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
INSERT OR IGNORE INTO revision VALUES(1,0);
""")
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def get(self,doc_id):
        with self.db() as c:r=c.execute("SELECT * FROM documents WHERE id=?",(doc_id,)).fetchone()
        if not r:return None
        d=dict(r);d["allowed_users"]=json.loads(d["allowed_users"]);return d
    def epoch(self):
        with self.db() as c:return c.execute("SELECT value FROM revision WHERE id=1").fetchone()[0]
    def register(self,doc_id,title,org,owner,visibility="management",allowed_users=None):
        if visibility not in VISIBILITIES or not org:raise ValueError("文档权限无效")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            old=c.execute("SELECT org_tag FROM documents WHERE id=?",(doc_id,)).fetchone()
            if old:raise ValueError("文档标识已存在，不能覆盖")
            c.execute("INSERT INTO documents(id,title,org_tag,owner_id,visibility,allowed_users,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (doc_id,title,org,owner,visibility,json.dumps(allowed_users or []),time.time()))
            c.execute("UPDATE revision SET value=value+1 WHERE id=1")
        return self.get(doc_id)
    def migrate_chunks(self,chunks,default_org):
        """Legacy public corpus becomes company scoped; legacy private remains management only."""
        with self.db() as c:
            changed=False
            for chunk in chunks:
                if not chunk.doc_id:continue
                org=chunk.org_tag or default_org
                r=c.execute("INSERT OR IGNORE INTO documents(id,title,org_tag,owner_id,visibility,allowed_users,updated_at) VALUES(?,?,?,?,?,'[]',?)",
                            (chunk.doc_id,chunk.doc_name,org,chunk.owner_id,"company" if chunk.is_public else "management",time.time()))
                changed=changed or bool(r.rowcount)
            if changed:c.execute("UPDATE revision SET value=value+1 WHERE id=1")
    def update(self,doc_id,org,visibility=None,allowed_users=None,title=None,delete=False):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            r=c.execute("SELECT * FROM documents WHERE id=? AND org_tag=? AND deleted=0",(doc_id,org)).fetchone()
            if not r:raise ValueError("文档不存在")
            v=visibility or r["visibility"]
            if v not in VISIBILITIES:raise ValueError("文档权限无效")
            c.execute("UPDATE documents SET title=?,visibility=?,allowed_users=?,deleted=?,version=version+1,updated_at=? WHERE id=?",
                      (title if title is not None else r["title"],v,json.dumps(allowed_users) if allowed_users is not None else r["allowed_users"],
                       int(delete),time.time(),doc_id))
            c.execute("UPDATE revision SET value=value+1 WHERE id=1")
        return self.get(doc_id)
    @staticmethod
    def allowed(record,permission):
        if not record or record["deleted"]:return False
        if permission.is_admin:return True  # Legacy development-only platform identity.
        from .accounts import get_account_store
        current=get_account_store().by_id(permission.user_id) if permission.user_id.startswith("user_") else None
        if permission.user_id.startswith("user_") and (not current or not current["active"]):return False
        roles={current["role"]} if current else set(permission.roles)
        org=current["org_tag"] if current else permission.org_tag
        if not org or record["org_tag"]!=org:return False
        if roles&{"admin","boss"}:return True
        return record["visibility"]=="company" or (
            record["visibility"]=="selected" and permission.user_id in record["allowed_users"])
    def can_read(self,doc_id,permission):
        return self.allowed(self.get(doc_id),permission)
    def list(self,permission):
        with self.db() as c:
            if permission.is_admin:rows=c.execute("SELECT * FROM documents WHERE deleted=0 ORDER BY updated_at DESC").fetchall()
            else:rows=c.execute("SELECT * FROM documents WHERE org_tag=? AND deleted=0 ORDER BY updated_at DESC",(permission.org_tag,)).fetchall()
        result=[]
        for row in rows:
            d=dict(row);d["allowed_users"]=json.loads(d["allowed_users"])
            if self.allowed(d,permission):result.append(d)
        return result
from functools import lru_cache
@lru_cache(maxsize=8)
def _access_for(path):return AccessStore(path)
def get_access_store():
    return _access_for(str(get_settings().data_dir/"state"/"document-access.sqlite"))
