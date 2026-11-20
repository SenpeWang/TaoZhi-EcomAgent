"""FastAPI 应用装配入口：全局安全响应头、异常映射、分领域路由与前端静态托管。"""
from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from taozhi.config import ROOT
from taozhi.domain.errors import Denied
from taozhi.server.routers import admin, auth, documents, system, tasks, traces

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

app = FastAPI(title="企业智能协作工作台", docs_url=None, redoc_url=None)


@app.middleware("http")
async def request_id(request: Request, call_next):
    """为每个 HTTP 请求分配链路追踪标识并附加安全响应头。"""
    request.state.request_id = secrets.token_hex(8)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Denied)
async def denied(request: Request, exc: Denied):
    """将领域拒绝异常映射为统一 JSON 错误码结构。"""
    return JSONResponse(
        {"code": exc.code, "message": exc.message, "request_id": getattr(request.state, "request_id", "")},
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def invalid(request: Request, exc: RequestValidationError):
    """将 Pydantic 校验错误统一映射为 422 中文提示。"""
    return JSONResponse(
        {
            "code": "INVALID_INPUT",
            "message": "输入格式不正确，请检查必填项、长度和数值范围",
            "request_id": getattr(request.state, "request_id", ""),
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    """兜底未捕获异常，防止泄露内部栈信息。"""
    return JSONResponse(
        {
            "code": "INTERNAL_ERROR",
            "message": "服务暂时无法处理请求，请稍后重试",
            "request_id": getattr(request.state, "request_id", ""),
        },
        status_code=500,
    )


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(tasks.router)
app.include_router(traces.router)

dist = ROOT / "web/dist"
if (dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """前端单页应用回落路由；未知 /api/ 路径返回 404。"""
    if path.startswith("api/"):
        raise Denied("接口不存在", "NOT_FOUND", 404)
    if not (dist / "index.html").exists():
        return Response("中文工作台正在准备", media_type="text/plain; charset=utf-8", status_code=503)
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
