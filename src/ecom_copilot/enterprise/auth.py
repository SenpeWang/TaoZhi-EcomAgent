import hashlib,secrets
from datetime import datetime,timezone,timedelta
from fastapi import Request
from .db import database_connection,fetch_one
from .config import load_config
from .passwords import verify_password,hash_password
from .policy import Denied,load_principal,audit
SESSION_COOKIE="ecom_session_"+load_config().mode
CSRF_COOKIE="ecom_csrf_"+load_config().mode
def hash_token(s):return hashlib.sha256(s.encode()).hexdigest()
_dummy=None
def login(username,password,ip,tenant_code):
    global _dummy
    if _dummy is None:_dummy=hash_password("dummy-password-for-timing","production")
    keys=["account:"+hash_token(tenant_code+":"+username.casefold()),"ip:"+hash_token(ip)]
    now=datetime.now(timezone.utc)
    with database_connection() as c:
        tenant=fetch_one(c,"SELECT * FROM tenants WHERE code=%s AND enabled",(tenant_code,))
        for key in keys:
            r=fetch_one(c,"SELECT * FROM login_attempts WHERE key=%s",(key,))
            if r and r["locked_until"] and r["locked_until"]>now:raise Denied("尝试次数过多，请 10 分钟后再试","LOGIN_LOCKED",429)
        row=fetch_one(c,"SELECT * FROM users WHERE tenant_id=%s AND username=%s",(tenant["id"],username.strip().casefold())) if tenant else None
    ok=verify_password(password,row["password_hash"] if row else _dummy)
    if not row or not row["active"] or not ok:
        with database_connection() as c:
            for key in keys:
                cap=5 if key.startswith("account:") else 30
                c.execute("INSERT INTO login_attempts(key,count,updated_at) VALUES(%s,1,now()) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN login_attempts.updated_at<now()-interval '10 minutes' THEN 1 ELSE login_attempts.count+1 END,updated_at=now()",(key,))
                c.execute("UPDATE login_attempts SET locked_until=CASE WHEN count>=%s THEN now()+interval '10 minutes' ELSE NULL END WHERE key=%s",(cap,key))
        raise Denied("账号或密码错误，或账号已停用","LOGIN_FAILED",401)
    with database_connection() as c:
        p=load_principal(c,row["id"])
        fresh=fetch_one(c,"SELECT * FROM users WHERE id=%s FOR UPDATE",(p.id,))
        if fresh["password_hash"]!=row["password_hash"] or fresh["version"]!=p.version:raise Denied("账号状态已变化，请重试","LOGIN_FAILED",401)
        if load_config().mode=="production" and verify_password("123456",fresh["password_hash"]):raise Denied("正式环境禁止演示口令，请通过管理员申请重置","DEMO_PASSWORD_FORBIDDEN",403)
        sid,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        expires=now+timedelta(hours=2)
        c.execute("INSERT INTO sessions(id,tenant_id,user_id,user_version,csrf_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",(hash_token(sid),p.tenant_id,p.id,p.version,hash_token(csrf),expires))
        c.execute("DELETE FROM login_attempts WHERE key=%s",(keys[0],))
        audit(c,p,"登录",system=True)
    return p,sid,csrf,expires
def identity(request:Request):
    sid=request.cookies.get(SESSION_COOKIE,"")
    if not sid:raise Denied("请先登录","LOGIN_REQUIRED",401)
    with database_connection() as c:
        s=fetch_one(c,"SELECT * FROM sessions WHERE id=%s AND NOT revoked AND expires_at>now()",(hash_token(sid),))
        if not s:raise Denied("登录已失效，请重新登录","SESSION_EXPIRED",401)
        p=load_principal(c,s["user_id"])
        if p.tenant_id!=s["tenant_id"] or p.version!=s["user_version"]:raise Denied("账号权限已变化，请重新登录","SESSION_CHANGED",401)
        if request.method not in ("GET","HEAD","OPTIONS"):
            import hmac
            token=request.headers.get("x-csrf-token","")
            origin=request.headers.get("origin")
            expected=str(request.base_url).rstrip("/")
            if not token or not hmac.compare_digest(hash_token(token),s["csrf_hash"]) or (origin and origin!=expected):
                raise Denied("请求校验失败，请刷新页面重试","CSRF_INVALID",403)
        if p.must_change and request.url.path not in ("/api/v2/auth/me","/api/v2/auth/password","/api/v2/auth/logout"):
            raise Denied("请先修改初始密码","PASSWORD_CHANGE_REQUIRED",403)
        return p
