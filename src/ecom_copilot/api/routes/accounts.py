"""中文账号登录、个人密码、企业人员管理和审计接口。"""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict,Field
from ...security.auth import User,current_user,require_permission,create_token
from ...security.accounts import get_account_store,AccountError,LoginError,LoginLocked
router=APIRouter(tags=["账号与权限"])
class Login(BaseModel):
    model_config=ConfigDict(extra="forbid")
    username:str=Field(min_length=3,max_length=64)
    password:str=Field(min_length=1,max_length=128)
class NewUser(Login):
    role:Literal["admin","boss","employee"]="employee"
    display_name:str=Field(default="",max_length=80)
class UpdateUser(BaseModel):
    model_config=ConfigDict(extra="forbid")
    role:Literal["admin","boss","employee"]|None=None
    active:bool|None=None
    display_name:str|None=Field(default=None,min_length=1,max_length=80)
    new_password:str|None=Field(default=None,min_length=12,max_length=128)
class Password(BaseModel):
    model_config=ConfigDict(extra="forbid")
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)
@router.post("/api/auth/login")
def login(payload:Login,request:Request):
    store=get_account_store()
    try:record=store.login(payload.username,payload.password,request.client.host if request.client else "local")
    except LoginLocked as e:raise HTTPException(429,str(e),headers={"Retry-After":"600"})
    except LoginError as e:raise HTTPException(401,str(e))
    sid,expires=store.create_session(record,120)
    user=User(user_id=record["id"],org_tag=record["org_tag"],roles=[record["role"]],username=record["username"],
              display_name=record["display_name"],session_id=sid,must_change_password=bool(record["must_change"]))
    return {"access_token":create_token(user,expires_minutes=120),"expires_at":expires,"user":user.public()}
@router.get("/api/auth/me")
def me(user:User=Depends(current_user)):return user.public()
@router.post("/api/auth/logout")
def logout(user:User=Depends(current_user)):
    if user.session_id:get_account_store().revoke(user.session_id)
    get_account_store().audit(user.user_id,user.org_tag,"退出登录")
    return {"message":"已退出登录"}
@router.post("/api/auth/password")
def password(payload:Password,user:User=Depends(current_user)):
    try:get_account_store().change_password(user,payload.current_password,payload.new_password)
    except AccountError as e:raise HTTPException(400,str(e))
    return {"message":"密码已修改，请使用新密码重新登录"}
@router.get("/api/users")
def users(user:User=Depends(require_permission("users:manage"))):
    return {"users":get_account_store().list_users(user.org_tag)}
@router.post("/api/users",status_code=201)
def create(payload:NewUser,user:User=Depends(require_permission("users:manage"))):
    try:record=get_account_store().create(payload.username,payload.password,payload.role,user.org_tag,payload.display_name,user.user_id)
    except AccountError as e:raise HTTPException(400,str(e))
    return record
@router.patch("/api/users/{uid}")
def update(uid:str,payload:UpdateUser,user:User=Depends(require_permission("users:manage"))):
    try:return get_account_store().update(user,uid,**payload.model_dump(exclude_none=True))
    except AccountError as e:raise HTTPException(400,str(e))
@router.get("/api/audit")
def audit(limit:int=Query(100,ge=1,le=200),user:User=Depends(require_permission("audit:read"))):
    return {"events":get_account_store().recent_audit(user.org_tag,limit)}
