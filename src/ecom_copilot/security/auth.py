"""企业登录身份与 RBAC。生产环境只接受可撤销的账号会话。"""
from __future__ import annotations
import time
from typing import Dict,List,Optional,Sequence
import jwt
from fastapi import Depends,HTTPException
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from pydantic import BaseModel,ConfigDict,Field
from ..config import Settings,get_settings
_bearer=HTTPBearer(auto_error=False)
_READ=["research:read","research:ask","knowledge:read"]
_MANAGE=_READ+["knowledge:write","knowledge:grant","research:review","users:manage","audit:read","metrics:read"]
ROLE_PERMISSIONS:Dict[str,List[str]]={
    "employee":_READ,"admin":_MANAGE,"boss":_MANAGE,
    # Legacy test/development roles; cannot authenticate without a real session in production.
    "viewer":["research:read"],"analyst":_READ+["research:write"],
    "reviewer":["research:read","research:review","knowledge:read"],
}
class User(BaseModel):
    model_config=ConfigDict(extra="forbid")
    user_id:str="anonymous"
    org_tag:str="default"
    roles:List[str]=Field(default_factory=lambda:["viewer"])
    is_admin:bool=False
    username:str=""
    display_name:str=""
    session_id:str=""
    must_change_password:bool=False
    def permissions(self):
        perms=[]
        for role in self.roles:perms.extend(ROLE_PERMISSIONS.get(role,[]))
        if self.is_admin:perms.append("admin:all")
        return list(dict.fromkeys(perms))
    def has(self,permission):
        return "admin:all" in self.permissions() or permission in self.permissions()
    def context(self):
        from ..schemas.common import PermissionContext
        return PermissionContext(user_id=self.user_id,org_tag=self.org_tag,roles=self.roles,is_admin=self.is_admin)
    def public(self):
        return {"user_id":self.user_id,"username":self.username,"display_name":self.display_name,
                "org_tag":self.org_tag,"roles":self.roles,"permissions":self.permissions(),
                "must_change_password":self.must_change_password}

def create_token(user:User,settings:Optional[Settings]=None,expires_minutes=None):
    cfg=settings or get_settings()
    payload={"sub":user.user_id,"org_tag":user.org_tag,"roles":user.roles,"is_admin":user.is_admin,
             "iat":int(time.time()),"exp":int(time.time())+60*(expires_minutes or cfg.jwt_expire_minutes)}
    if user.session_id:payload["sid"]=user.session_id
    return jwt.encode(payload,cfg.jwt_secret,algorithm=cfg.jwt_algorithm)

def decode_token(token,settings=None):
    cfg=settings or get_settings()
    try:payload=jwt.decode(token,cfg.jwt_secret,algorithms=[cfg.jwt_algorithm],options={"require":["exp","iat","sub"]})
    except jwt.ExpiredSignatureError:raise HTTPException(401,"登录已过期，请重新登录") from None
    except jwt.PyJWTError:raise HTTPException(401,"登录凭证无效，请重新登录") from None
    if payload.get("sid"):
        from .accounts import get_account_store
        record=get_account_store().session_user(str(payload["sid"]),str(payload["sub"]))
        if not record:raise HTTPException(401,"登录已失效或账号已停用，请重新登录")
        return User(user_id=record["id"],org_tag=record["org_tag"],roles=[record["role"]],
                    username=record["username"],display_name=record["display_name"],
                    session_id=payload["sid"],must_change_password=bool(record["must_change"]))
    if cfg.app_env.lower() in ("prod","production"):
        raise HTTPException(401,"请使用账号和密码登录")
    return User(user_id=str(payload["sub"]),org_tag=str(payload.get("org_tag","default")),
                roles=list(payload.get("roles",[]) or ["viewer"]),is_admin=bool(payload.get("is_admin",False)))

def current_user(credentials:Optional[HTTPAuthorizationCredentials]=Depends(_bearer),settings:Settings=Depends(get_settings)):
    if not settings.auth_enabled:return User(user_id="anonymous",org_tag="default",roles=["admin"],is_admin=True)
    if credentials is None or not credentials.credentials:raise HTTPException(401,"请先登录")
    return decode_token(credentials.credentials,settings)

def require_permission(permission):
    def dep(user:User=Depends(current_user)):
        if settings_auth_enabled():
            if user.must_change_password:raise HTTPException(403,"首次登录请先修改临时密码")
            # Query execution may persist a job, but does not grant knowledge management access.
            permitted=user.has(permission) or (permission=="research:ask" and user.has("research:write"))
            if not permitted:raise HTTPException(403,"当前账号没有执行此操作的权限")
        return user
    return dep

def settings_auth_enabled():return get_settings().auth_enabled
def sanitize_roles(roles:Sequence[str]):return [r for r in roles if r in ROLE_PERMISSIONS] or ["viewer"]
def identity(user,user_id="",org_tag=""):
    if not get_settings().auth_enabled:return user_id or user.user_id,org_tag or user.org_tag
    if (user_id and user_id!=user.user_id) or (org_tag and org_tag!=user.org_tag):
        raise HTTPException(403,"请求身份与登录身份不一致")
    return user.user_id,user.org_tag
