"""ChatBI 受控问数工作流状态契约。"""
from __future__ import annotations

from typing import TypedDict


class BiState(TypedDict, total=False):
    """ChatBI 指标问数 LangGraph 状态契约。"""

    job_id: str
    run_version: int
    question: str
    data_intent: dict
    data_result: dict
    answer: str
    hard_block: bool
    input_level: int
    timing: dict
    token_usage: dict
