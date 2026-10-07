"""本地企业账号、密码哈希、可撤销会话、登录限流及安全审计。"""
from __future__ import annotations
import hashlib,hmac,json,os,secrets,sqlite3,time,threading
from contextlib import contextmanager
from pathlib import Path
from ..config import get_settings

ROLES={"admin":"管理员","boss":"老板","employee":"员工"}
class AccountError(ValueError):pass
class LoginError(AccountError):pass
class LoginLocked(LoginError):pass

_HASH_SLOTS=threading.BoundedSemaphore(4)
def _derive(password,salt,parallelism=5):
    with _HASH_SLOTS:
        return hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=parallelism,dklen=32,maxmem=64*1024*1024)

def password_hash(password):
    if not isinstance(password,str) or not 12<=len(password)<=128:
        raise AccountError("密码须为 12–128 个字符")
    salt=secrets.token_bytes(16)
    # OWASP scrypt profile: N=2^14, r=8, p=5. Parameters travel with the hash.
    return "scrypt$16384$8$5$"+salt.hex()+"$"+_derive(password,salt).hex()

def password_matches(password,stored):
    try:
        parts=stored.split("$")
        if len(parts)==3:
            scheme,salt,digest=parts;parallelism=1  # One-time bootstrap migration.
        elif len(parts)==6:
            scheme,n,r,p,salt,digest=parts
            if (n,r,p)!=("16384","8","5"):return False
            parallelism=5
        else:return False
        if scheme!="scrypt" or len(password)>128 or len(salt)!=32 or len(digest)!=64:return False
        actual=_derive(password,bytes.fromhex(salt),parallelism)
        return hmac.compare_digest(actual.hex(),digest)
    except (ValueError,TypeError):return False

_DUMMY=password_hash("dummy-password-for-timing")
class AccountStore:
    def __init__(self,path=None):
        self.path=Path(path or get_settings().data_dir/"state"/"accounts.sqlite")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,display_name TEXT NOT NULL,
org_tag TEXT NOT NULL,role TEXT NOT NULL,password_hash TEXT NOT NULL,active INTEGER NOT NULL,
must_change INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,version INTEGER NOT NULL,
expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,locked_until REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT,org_tag TEXT,action TEXT,target TEXT,detail TEXT,created_at REAL);
""")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()

    @staticmethod
    def public(row):
        return {k:row[k] for k in ("id","username","display_name","org_tag","role","active","must_change","version")}

    def audit(self,actor,org,action,target="",detail=None):
        with self.db() as c:
            c.execute("INSERT INTO audit(actor,org_tag,action,target,detail,created_at) VALUES(?,?,?,?,?,?)",
                      (actor,org,action,target,json.dumps(detail or {},ensure_ascii=False),time.time()))

    def create(self,username,password,role,org,display_name="",actor="bootstrap",must_change=True):
        username=username.strip().casefold()
        if not 3<=len(username)<=64 or not all(ch.isalnum() or ch in "._-" for ch in username):
            raise AccountError("账号须为 3–64 个字母、数字、中文或 ._-")
        if role not in ROLES or not org.strip():raise AccountError("角色或企业无效")
        hashed=password_hash(password);uid="user_"+secrets.token_hex(12)
        with self.db() as c:
            try:c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,1,?,1,?)",
                          (uid,username,(display_name.strip() or username)[:80],org,role,hashed,int(must_change),time.time()))
            except sqlite3.IntegrityError:raise AccountError("账号名称已存在") from None
        self.audit(actor,org,"创建账号",uid,{"role":role})
        return self.by_id(uid)

    def by_id(self,uid):
        with self.db() as c:r=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        return self.public(r) if r else None

    def list_users(self,org):
        with self.db() as c:rows=c.execute("SELECT * FROM users WHERE org_tag=? ORDER BY created_at",(org,)).fetchall()
        return [self.public(r) for r in rows]

    def login(self,username,password,client_ip="local"):
        username=username.strip().casefold();now=time.time()
        # Name lock applies across source IPs; source lock also throttles random usernames.
        keys=["name:"+hashlib.sha256(username.encode()).hexdigest(),"ip:"+hashlib.sha256(client_ip.encode()).hexdigest()]
        with self.db() as c:
            for key in keys:
                row=c.execute("SELECT * FROM attempts WHERE key=?",(key,)).fetchone()
                if row and row["locked_until"]>now:raise LoginLocked("尝试次数过多，请 10 分钟后重试")
            row=c.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
        ok=password_matches(password,row["password_hash"] if row else _DUMMY)
        if not row or not row["active"] or not ok:
            with self.db() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM attempts WHERE updated_at<?",(now-86400,))
                for key in keys:
                    old=c.execute("SELECT * FROM attempts WHERE key=?",(key,)).fetchone()
                    count=old["count"]+1 if old and old["updated_at"]>now-600 else 1
                    cap=5 if key.startswith("name:") else 30
                    c.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)",(key,count,now+600 if count>=cap else 0,now))
            self.audit("",row["org_tag"] if row else "","登录失败","",{"account_hash":keys[0][5:]})
            raise LoginError("账号或密码错误，或账号已停用")
        with self.db() as c:
            c.execute("DELETE FROM attempts WHERE key=?",(keys[0],))
            if len(row["password_hash"].split("$"))==3:
                c.execute("UPDATE users SET password_hash=? WHERE id=?",(password_hash(password),row["id"]))
        self.audit(row["id"],row["org_tag"],"登录")
        return self.public(row)

    def create_session(self,user,minutes=120):
        sid=secrets.token_urlsafe(32);expires=int(time.time())+minutes*60
        with self.db() as c:
            c.execute("DELETE FROM sessions WHERE expires_at<?",(time.time(),))
            c.execute("INSERT INTO sessions VALUES(?,?,?,?,0)",(hashlib.sha256(sid.encode()).hexdigest(),user["id"],user["version"],expires))
        return sid,expires

    def session_user(self,sid,uid):
        with self.db() as c:
            r=c.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
                        "WHERE s.id=? AND u.id=? AND u.active=1 AND s.revoked=0 "
                        "AND s.expires_at>? AND s.version=u.version",
                        (hashlib.sha256(sid.encode()).hexdigest(),uid,time.time())).fetchone()
        return self.public(r) if r else None

    def revoke(self,sid):
        with self.db() as c:c.execute("UPDATE sessions SET revoked=1 WHERE id=?",(hashlib.sha256(sid.encode()).hexdigest(),))

    def update(self,actor,uid,role=None,active=None,display_name=None,new_password=None):
        hashed=password_hash(new_password) if new_password is not None else None
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT * FROM users WHERE id=? AND org_tag=?",(uid,actor.org_tag)).fetchone()
            if not row:raise AccountError("账号不存在")
            next_role=role if role is not None else row["role"]
            next_active=int(active) if active is not None else row["active"]
            if next_role not in ROLES:raise AccountError("角色无效")
            if row["role"] in ("admin","boss") and row["active"] and (not next_active or next_role=="employee"):
                count=c.execute("SELECT count(*) FROM users WHERE org_tag=? AND active=1 AND role IN ('admin','boss')",(actor.org_tag,)).fetchone()[0]
                if count<=1:raise AccountError("不能停用或降级企业最后一位管理员/老板")
            c.execute("UPDATE users SET role=?,active=?,display_name=?,password_hash=?,must_change=?,version=version+1 WHERE id=?",
                      (next_role,next_active,display_name if display_name is not None else row["display_name"],
                       hashed or row["password_hash"],1 if hashed else row["must_change"],uid))
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?",(uid,))
        self.audit(actor.user_id,actor.org_tag,"修改账号",uid,{"role":next_role,"active":bool(next_active),"reset_password":bool(hashed)})
        return self.by_id(uid)

    def change_password(self,user,current,new):
        hashed=password_hash(new)
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT * FROM users WHERE id=? AND active=1",(user.user_id,)).fetchone()
            if not row or not password_matches(current,row["password_hash"]):raise AccountError("原密码不正确")
            if password_matches(new,row["password_hash"]):raise AccountError("新密码不能与原密码相同")
            c.execute("UPDATE users SET password_hash=?,must_change=0,version=version+1 WHERE id=?",(hashed,user.user_id))
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?",(user.user_id,))
        self.audit(user.user_id,user.org_tag,"修改本人密码",user.user_id)
        bootstrap=Path.home()/".local/share/ecom-copilot/bootstrap-admin.json"
        if bootstrap.exists():
            try:
                if json.loads(bootstrap.read_text()).get("user_id")==user.user_id:bootstrap.unlink()
            except (ValueError,OSError):pass

    def recent_audit(self,org,limit=100):
        with self.db() as c:rows=c.execute("SELECT * FROM audit WHERE org_tag=? ORDER BY id DESC LIMIT ?",(org,limit)).fetchall()
        return [dict(r) for r in rows]

from functools import lru_cache
@lru_cache(maxsize=8)
def _accounts_for(path):return AccountStore(path)
def get_account_store():
    return _accounts_for(str(get_settings().data_dir/"state"/"accounts.sqlite"))
