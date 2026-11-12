# 领域纯规则：不接受数据库连接 conn，只做纯内存判定
from __future__ import annotations
from typing import Optional
from .entities import Principal

def can_task(p: Principal, job: dict) -> bool:
    if job.get("tenant_id") != p.tenant_id:
        return False
    return p.id == job.get("owner_id") or (p.manages(job.get("node_id")) and p.grade(job.get("node_id")) >= job.get("input_level", 1))

def can_review(p: Principal, job: dict) -> bool:
    return job.get("tenant_id") == p.tenant_id and p.manages(job.get("node_id")) and p.grade(job.get("node_id")) >= job.get("input_level", 1)

def evaluate_read_permission(p: Principal, doc: dict, grant: Optional[dict] = None, include_draft: bool = False, can_write_flag: bool = False) -> bool:
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
    if doc.get("tenant_id") != p.tenant_id or doc.get("state") == "deleted":
        return False
    if grant and grant.get("effect") == "deny":
        return False
    return p.boss or (p.manages(doc.get("node_id")) and doc.get("level", 1) <= 2 and doc.get("scope") != "company") or (p.system_admin and doc.get("scope") == "company" and doc.get("level") == 1)

def evaluate_download_permission(p: Principal, doc: dict, grant: Optional[dict] = None, can_read_flag: bool = False) -> bool:
    if not can_read_flag:
        return False
    if grant and grant.get("effect") == "deny":
        return False
    return p.boss or bool(grant and grant.get("effect") == "allow" and grant.get("can_download")) or (doc.get("scope") != "selected" and p.grade(doc.get("node_id"), doc.get("scope", "org")) >= doc.get("level", 1) and doc.get("download_allowed", False))
