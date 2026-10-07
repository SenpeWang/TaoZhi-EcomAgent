"""健康检查 / 指标 / 鉴权调试接口。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from ...config import Settings, get_settings
from ...observability import agent_success_rate, rag_hit_rate, recent_events, snapshot
from ...security.auth import User, create_token, current_user, require_permission

router = APIRouter(tags=["system"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = "u123"
    org_tag: str = "新能源组"
    roles: list = ["analyst"]
    is_admin: bool = False


@router.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "2.1.0",
        "llm_configured": settings.has_llm,
        "llm_model": settings.llm_strong_model,
        "backends": {
            "milvus": settings.milvus_enabled,
            "elasticsearch": settings.es_enabled,
            "neo4j": settings.neo4j_enabled,
            "minio": settings.minio_enabled,
            "kafka": settings.kafka_enabled,
            "redis": settings.redis_enabled,
        },
        "auth_enabled": settings.auth_enabled,
    }


@router.get("/api/metrics")
async def metrics(user: User = Depends(require_permission("admin:all"))) -> Dict[str, Any]:
    snap = snapshot()
    snap["rag.hit_rate"] = rag_hit_rate()
    snap["agent.success_rate.supervisor"] = agent_success_rate("supervisor")
    return {"metrics": snap, "events": recent_events(30)}


@router.post("/api/auth/token")
async def issue_token(payload: LoginRequest,
                      settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """开发态签发 JWT（生产环境应接入企业 SSO）。"""
    if settings.auth_enabled or settings.app_env.lower() in ("prod", "production"):
        raise HTTPException(status_code=404, detail="生产环境请使用企业身份认证")
    user = User(user_id=payload.user_id, org_tag=payload.org_tag,
                roles=payload.roles, is_admin=payload.is_admin)
    return {"access_token": create_token(user, settings), "token_type": "bearer",
            "expires_minutes": settings.jwt_expire_minutes, "user": user.model_dump()}
