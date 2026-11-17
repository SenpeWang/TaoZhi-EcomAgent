"""组织架构、企业账号管理与双人复核审批流路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from taozhi.application.services import admin_service
from taozhi.schemas.admin import Membership, NewUser, Org, OrgChange, UserChange
from taozhi.schemas.common import Decision
from taozhi.server.auth import identity

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/organizations")
def organizations(p=Depends(identity)):
    """列出当前租户组织树节点。"""
    return {"items": list(p.nodes.values())}


@router.post("/organizations", status_code=201)
def add_org(payload: Org, p=Depends(identity)):
    """新增部门或小组组织节点。"""
    return admin_service.create_organization(p, name=payload.name, kind=payload.kind, parent_id=payload.parent_id)


@router.patch("/organizations/{node_id}", status_code=202)
def update_org(node_id: str, payload: OrgChange, p=Depends(identity)):
    """提交组织节点调整审批申请。"""
    return admin_service.request_organization_change(p, node_id, payload.model_dump(exclude_none=True))


@router.get("/users")
def users(p=Depends(identity)):
    """按组织管理范围列出可见成员列表。"""
    return {"items": admin_service.list_visible_users(p)}


@router.post("/users", status_code=201)
def add_user(payload: NewUser, p=Depends(identity)):
    """创建待分配组织与角色的新账号。"""
    return admin_service.create_user(
        p, username=payload.username, password=payload.password, display_name=payload.display_name
    )


@router.patch("/users/{user_id}", status_code=202)
def change_user(user_id: str, payload: UserChange, p=Depends(identity)):
    """提交账号信息或启停变更审批申请。"""
    return admin_service.request_user_change(p, user_id, payload.model_dump(exclude_none=True))


@router.post("/users/{user_id}/memberships", status_code=202)
def assign(user_id: str, payload: Membership, p=Depends(identity)):
    """提交组织任职授权或解除审批申请。"""
    return admin_service.request_membership_change(
        p, user_id, node_id=payload.node_id, role=payload.role, remove=payload.remove
    )


@router.get("/approvals")
def approvals(p=Depends(identity)):
    """查询待当前用户处理或发起的审批申请单。"""
    return {"items": admin_service.list_approvals_for_principal(p)}


@router.post("/approvals/{aid}/decision")
def decision(aid: str, payload: Decision, p=Depends(identity)):
    """审批通过或拒绝目标申请单。"""
    return admin_service.decide_approval(p, aid, approve=payload.approve, comment=payload.comment)
