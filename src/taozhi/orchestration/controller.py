"""编排层确定性控制守护器：实现最大迭代控制、死锁熔断与硬边界防御。"""
from __future__ import annotations

import difflib
import time
from typing import List, Tuple

from .state import OrchestrationState


class DeterministicController:
    """确定性控制守护器。终止条件必须由确定性代码控制，严禁全交由模型判断。"""

    def __init__(self, max_iterations: int = 3, timeout_seconds: float = 30.0) -> None:
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    def check_iteration_budget(self, state: OrchestrationState) -> Tuple[bool, str]:
        """检查迭代预算。返回 (是否允许继续, 状态原因)。"""
        if state.iteration_count >= self.max_iterations:
            return False, "MAX_ITERATIONS_EXCEEDED"
        return True, "BUDGET_OK"

    def check_timeout(self, start_time: float) -> Tuple[bool, str]:
        """检查全局任务超时。"""
        elapsed = time.time() - start_time
        if elapsed > self.timeout_seconds:
            return False, "ORCHESTRATION_TIMEOUT"
        return True, "TIME_OK"

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