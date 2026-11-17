"""FastAPI 分领域 HTTP 路由集合。"""
from __future__ import annotations

from taozhi.server.routers import admin, auth, documents, system, tasks

__all__ = ["admin", "auth", "documents", "system", "tasks"]
