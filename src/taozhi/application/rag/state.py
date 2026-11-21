"""企业知识问答 RAG 工作流状态结构。"""
from __future__ import annotations

from typing import TypedDict


class State(TypedDict, total=False):
    """企业知识问答 LangGraph 状态契约。"""

    job_id: str
    run_version: int
    question: str
    selected_agents: list
    evidence: list
    findings: list
    missing: list
    model_calls: int
    hard_block: bool
    answer: str
    sources: list
    external_blocked: bool
    business: list
    review: bool
    input_level: int
    usage: list
    timing: dict
    degraded: dict
    token_usage: dict
