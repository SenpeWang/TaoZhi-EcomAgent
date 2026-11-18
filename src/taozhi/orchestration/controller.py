"""编排层确定性控制守护器：实现改写死锁检测。"""
from __future__ import annotations

import difflib
from typing import List


class DeterministicController:
    """确定性控制守护器，拦截检索改写死循环。"""

    def __init__(self, max_iterations: int = 3, timeout_seconds: float = 30.0) -> None:
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    def detect_rewrite_deadlock(self, new_query: str, history: List[str]) -> bool:
        """检测改写死循环（语义高度重合或完全重复）。"""
        clean_new = new_query.strip().lower()
        if not clean_new:
            return True

        for prev in history:
            clean_prev = prev.strip().lower()
            if clean_new == clean_prev:
                return True
            ratio = difflib.SequenceMatcher(None, clean_new, clean_prev).ratio()
            if ratio >= 0.90:
                return True
        return False