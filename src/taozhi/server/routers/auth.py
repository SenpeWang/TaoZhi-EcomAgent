"""用户登录、退出与本人密码修改路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from taozhi.application.services import admin_service
from taozhi.config import load_config
from taozhi.schemas.auth import Login, Password
from taozhi.server.auth import CSRF_COOKIE, SESSION_COOKIE, hash_token, identity, login

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def signin(payload: Login, request: Request, response: Response):
    """校验身份并签发 HttpOnly 会话 Cookie 与 CSRF 令牌。"""
    p, sid, csrf, expires = login(
        payload.username,
        payload.password,
        request.client.host if request.client else "local",
        payload.tenant_code or load_config().tenant_code,
    )
    cookie_secure = load_config().cookie_secure
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, secure=cookie_secure, samesite="strict", max_age=7200, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=cookie_secure, samesite="strict", max_age=7200, path="/")
    return dict(user=p.public(), csrf_token=csrf, expires_at=expires, message="登录成功")


@router.get("/me")
def me(p=Depends(identity)):
    """返回当前登录主体公开权限视图。"""
    return p.public()


@router.post("/logout")
def logout(request: Request, response: Response, p=Depends(identity)):
    """吊销服务端会话并清除客户端 Cookie。"""
    result = admin_service.revoke_user_session(p, hash_token(request.cookies.get(SESSION_COOKIE, "")))
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return result


@router.post("/password")
def password(payload: Password, p=Depends(identity)):
    """修改当前登录用户本人密码。"""
    return admin_service.change_own_password(p, payload.current_password, payload.new_password)
