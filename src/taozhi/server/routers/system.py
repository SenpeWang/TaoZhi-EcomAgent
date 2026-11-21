"""系统健康检查、工作台概览、经营看板、审计与知识图谱路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from taozhi.application.bi.dashboard import get_bi_dashboard, get_dashboard_summary
from taozhi.application.services import admin_service, document_service
from taozhi.server.auth import identity

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    """数据库连通性与工作节点运行健康探针。"""
    return admin_service.get_health_status()


@router.get("/info")
def info():
    """读取租户工作台基础配置与快捷登录视图。"""
    return admin_service.get_portal_info()


@router.get("/system/status")
def system_status(p=Depends(identity)):
    """管理员查询模型、业务中台与向量检索就绪度。"""
    return admin_service.get_system_status(p)


@router.get("/audit")
def audit_list(p=Depends(identity)):
    """按组织范围与角色密级过滤安全审计流水。"""
    return {"items": admin_service.list_audit_events_for_principal(p)}


@router.get("/dashboard")
def dashboard(p=Depends(identity)):
    """返回工作台首页聚合统计。"""
    return get_dashboard_summary(p)


@router.get("/bi/dashboard")
def bi_dashboard(days: int = Query(30, ge=7, le=365), p=Depends(identity)):
    """数据中台经营看板：全部指标源自语义层受控执行。"""
    return get_bi_dashboard(p, days)


@router.get("/knowledge-map")
def knowledge_map(p=Depends(identity)):
    """查询用户可见的已发布文档实体关系图谱。"""
    return {"items": document_service.list_knowledge_map(p)}
