"""领域权限判定纯规则（无数据库连接，仅基于主体与文档元数据计算）。"""
from __future__ import annotations

from typing import Optional

from .entities import Principal


RESTRICTED_QUESTION = "来源权限已变化，请重新查询"


def can_task(p: Principal, job: dict) -> bool:
    """判定主体是否有权查看或操作指定任务记录。

    Args:
        p: 当前经过鉴权的用户主体。
        job: 任务字典记录。

    Returns:
        有权访问时返回 True。
    """
    if job.get("tenant_id") != p.tenant_id:
        return False
    return p.id == job.get("owner_id") or (
        p.manages(job.get("node_id")) and p.grade(job.get("node_id")) >= job.get("input_level", 1)
    )


def can_review(p: Principal, job: dict) -> bool:
    """判定主体是否具备任务业务答案的审批权。

    Args:
        p: 当前经过鉴权的用户主体。
        job: 任务字典记录。

    Returns:
        可参与审核时返回 True。
    """
    return (
        job.get("tenant_id") == p.tenant_id
        and p.manages(job.get("node_id"))
        and p.grade(job.get("node_id")) >= job.get("input_level", 1)
    )


def evaluate_read_permission(
    p: Principal,
    doc: dict,
    grant: Optional[dict] = None,
    include_draft: bool = False,
    can_write_flag: bool = False,
) -> bool:
    """按文档范围、密级与个人特例授权判定阅读权限。

    Args:
        p: 当前用户主体。
        doc: 文档记录字典。
        grant: 该用户针对本文档的有效个人授权记录。
        include_draft: 是否允许有写权限者读取非发布态草稿。
        can_write_flag: 调用方预判的写权限标志。

    Returns:
        允许阅读返回 True，否则返回 False。
    """
    if doc.get("tenant_id") != p.tenant_id or doc.get("state") == "deleted":
        return False
    if grant and grant.get("effect") == "deny":
        return False
    if doc.get("state") != "published":
        return include_draft and can_write_flag
    if grant and grant.get("effect") == "allow" and grant.get("can_read"):
        return True
    if p.boss:
        return True
    return doc.get("scope") != "selected" and p.grade(doc.get("node_id"), doc.get("scope", "org")) >= doc.get("level", 1)


def evaluate_write_permission(p: Principal, doc: dict, grant: Optional[dict] = None) -> bool:
    """判定主体是否可编辑、发布初审或逻辑删除文档。

    Args:
        p: 当前用户主体。
        doc: 文档记录字典。
        grant: 该用户针对本文档的有效个人授权记录。

    Returns:
        允许写入返回 True，否则返回 False。
    """
    if doc.get("tenant_id") != p.tenant_id or doc.get("state") == "deleted":
        return False
    if grant and grant.get("effect") == "deny":
        return False
    return (
        p.boss
        or (p.manages(doc.get("node_id")) and doc.get("level", 1) <= 2 and doc.get("scope") != "company")
        or (p.system_admin and doc.get("scope") == "company" and doc.get("level") == 1)
    )


def evaluate_download_permission(
    p: Principal,
    doc: dict,
    grant: Optional[dict] = None,
    can_read_flag: bool = False,
) -> bool:
    """判定主体是否有权下载文档内容或原始附件。

    Args:
        p: 当前用户主体。
        doc: 文档记录字典。
        grant: 该用户针对本文档的有效个人授权记录。
        can_read_flag: 调用方预判的阅读权限标志。

    Returns:
        允许下载返回 True，否则返回 False。
    """
    if not can_read_flag:
        return False
    if grant and grant.get("effect") == "deny":
        return False
    return (
        p.boss
        or bool(grant and grant.get("effect") == "allow" and grant.get("can_download"))
        or (
            doc.get("scope") != "selected"
            and p.grade(doc.get("node_id"), doc.get("scope", "org")) >= doc.get("level", 1)
            and doc.get("download_allowed", False)
        )
    )


def evaluate_metric_read_permission(
    p: Principal,
    metric: dict,
    grant: Optional[dict] = None,
) -> bool:
    """按指标租户、发布状态、密级与个人特例授权判定可见性（与文档权限同构）。

    Args:
        p: 当前用户主体。
        metric: 指标定义字典。
        grant: 该用户针对本指标的个人特例授权记录。

    Returns:
        允许查询返回 True，否则返回 False。
    """
    if metric.get("tenant_id") != p.tenant_id or metric.get("state") != "published":
        return False
    if grant and grant.get("effect") == "deny":
        return False
    if grant and grant.get("effect") == "allow":
        return True
    if p.boss:
        return True
    return metric.get("scope") != "selected" and p.grade(metric.get("node_id"), metric.get("scope", "org")) >= metric.get("level", 1)
