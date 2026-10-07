"""FastAPI 应用入口：REST API + SSE + MCP 工具接口。"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..observability import enable_langsmith, inc
from .routes import accounts, graph, health, knowledge, mcp, research
from .limits import RequestBodyLimit

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    enable_langsmith()

    app = FastAPI(
        title=settings.app_name,
        version="2.1.0",
        description="手机配件企业工作台：账号登录、角色权限、文档授权、多智能体问答与审核",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RequestBodyLimit,max_upload_bytes=settings.max_upload_bytes)

    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(research.router)
    app.include_router(graph.router)
    app.include_router(knowledge.router)
    app.include_router(mcp.router)

    @app.on_event("startup")
    async def _startup() -> None:
        from ..retrieval import get_retrieval_engine

        engine = get_retrieval_engine()
        loaded = engine.load()
        if loaded:
            logger.info("已加载本地索引：%s 个切片", loaded)
        from ..security.accounts import get_account_store
        from ..security.access import get_access_store
        get_account_store()
        get_access_store().migrate_chunks(engine.index.chunks,settings.auth_default_org)
        from .tasks import get_task_manager
        get_task_manager()  # Acquire owner lock and recover interrupted tasks before serving.
        inc("app.startup")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        import asyncio
        from . import tasks
        if tasks._manager is not None:
            await asyncio.to_thread(tasks._manager.close)
            tasks._manager = None

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def _validation(request,exc):
        fields=[".".join(str(x) for x in e["loc"][1:]) for e in exc.errors()]
        return JSONResponse(status_code=422,content={"detail":"输入格式不正确，请检查字段："+", ".join(fields)})
    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # noqa: ANN001
        logger.error("unhandled request error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500,
                            content={"detail": "服务暂时不可用，请联系管理员"})

    @app.get("/")
    async def root() -> Dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": "2.1.0",
            "docs": "/docs",
            "endpoints": [
                "POST /api/research/task",
                "GET  /api/research/task/{task_id}/stream",
                "POST /api/graph/query",
                "POST /api/mcp/tools",
                "GET  /api/health",
            ],
        }

    return app


app = create_app()
