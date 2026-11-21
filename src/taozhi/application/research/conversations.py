"""市场调研的会话查询、记忆展示、中途干预和删除。"""
from __future__ import annotations

import logging
import secrets
from typing import Any

from taozhi.domain.access import RESTRICTED_QUESTION
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence import job_repo
from taozhi.infrastructure.persistence.access_repo import audit, batch_sources_validity
from taozhi.knowledge.db import database_connection

_LOG = logging.getLogger(__name__)


def list_research_conversations(p: Principal) -> list[dict[str, Any]]:
    """列出用户个人的多轮市场调研会话摘要清单。"""
    with database_connection() as c:
        rows = job_repo.list_research_conversation_summaries(c, p.tenant_id, p.id)
        valid = batch_sources_validity(c, p, rows)
        return [
            {
                "conversation_id": r["conversation_id"],
                "title": ((r["first_payload"] or {}).get("keyword") or r["title"])
                if valid[r["id"]] and r["first_state"] != "legacy" else RESTRICTED_QUESTION,
                "turns": r["turns"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else "",
                "last_active": r["last_active"].isoformat() if r["last_active"] else "",
                "latest_state": r["latest_state"],
            }
            for r in rows
        ]


def get_research_conversation(p: Principal, conversation_id: str) -> dict[str, Any]:
    """读取单个调研会话的全部轮次详情与长期记忆三元组。"""
    with database_connection() as c:
        rows = job_repo.list_research_conversation_turns(c, p.tenant_id, p.id, conversation_id)
        if not rows:
            return {"conversation_id": conversation_id, "turns": [], "context_summary": "", "memory_facts": []}
        turns = []
        facts = {}
        for r in rows:
            pj = job_repo.present_job(c, p, r, True)
            pj["thinking_logs"] = [e["stage"] for e in pj["events"] if e.get("stage")]
            visible_result = pj["result"] or {}
            calc_elapsed = (
                round((r["finished_at"] - r["started_at"]).total_seconds(), 1)
                if r.get("finished_at") and r.get("started_at")
                else 0
            )
            pj["elapsed"] = visible_result.get("elapsed") or calc_elapsed
            if pj["state"] == "completed":
                subject = visible_result.get("keyword") or pj["question"]
                for item in visible_result.get("evidence_pool") or []:
                    if item.get("status") == "ok" and item.get("summary"):
                        role = agent_display_name(str(item.get("role") or "调研洞察"))
                        summary = str(item["summary"])[:300]
                        facts[(subject, role)] = {"subject": subject, "predicate": role, "object": summary if p.boss else redact(summary)}
            turns.append(pj)
        latest_res = turns[-1]["result"] or {}
        first = turns[0]
        kw = first["question"] if first["restricted"] else (rows[0].get("payload") or {}).get("keyword") or first["question"]
    return {
        "conversation_id": conversation_id,
        "keyword": kw,
        "turns": turns,
        "context_summary": latest_res.get("context_summary", ""),
        "context_tokens": latest_res.get("context_tokens", 0),
        "is_compacted": latest_res.get("is_compacted", False),
        "memory_facts": list(facts.values())[-20:],
    }


def delete_research_conversation(p: Principal, conversation_id: str) -> dict[str, str]:
    """删除个人调研会话及其全部任务与事件流记录。"""
    with database_connection() as c:
        job_repo.delete_research_conversation_records(c, p.tenant_id, p.id, conversation_id)
        audit(c, p, "删除调研对话", conversation_id, p.root, 1)
    return {"message": "对话已成功删除"}


def steer_task(p: Principal, task_id: str, *, text: str, request_id: str = "") -> dict[str, Any]:
    """向运行中的协同调研任务注入幂等中途干预指令。"""
    steer_id = request_id.strip() or f"steer_{secrets.token_hex(8)}"
    with database_connection() as c:
        r = job_repo.get_job(c, p.tenant_id, task_id, for_update=True)
        if not r:
            raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
        if r["state"] not in ("queued", "running"):
            raise Denied("只能对进行中的任务追加干预指令", "CONFLICT", 409)
        if not (r["owner_id"] == p.id or p.boss or p.system_admin):
            raise Denied("无权干预该任务", "FORBIDDEN", 403)
        existing_items = ((r.get("payload") or {}).get("steering_inputs")) or []
        if any(isinstance(item, dict) and str(item.get("id") or "") == steer_id for item in existing_items):
            return {"message": "中途干预指令已受理（幂等命中）", "task_id": task_id, "input": text, "request_id": steer_id}
        run_ver = r.get("run_version") or 1
        job_repo.append_steering_to_job(c, r, steer_id, text)
        audit(c, p, "中途动态干预", task_id, r["node_id"], r["input_level"], detail={"request_id": steer_id})

    try:
        from taozhi.application.research.graph import build_autonomous_agent_graph
        from taozhi.infrastructure.persistence.checkpoints import postgres_checkpointer

        with postgres_checkpointer() as saver:
            graph = build_autonomous_agent_graph(checkpointer=saver, require_checkpointer=True)
            thread_id = f"research_{task_id}_{run_ver}"
            cfg = {"configurable": {"thread_id": thread_id}}
            snap = graph.get_state(cfg)
            seen_ids = set((snap.values or {}).get("steering_event_ids") or []) if snap else set()
            if steer_id not in seen_ids:
                graph.update_state(cfg, {"steering_inbox": [text], "steering_event_ids": [steer_id]})
    except Exception as exc:
        _LOG.warning("检查点即时注入失败，使用数据库队列（%s）", type(exc).__name__)
    return {"message": "中途干预指令已发送并生效", "task_id": task_id, "input": text, "request_id": steer_id}
