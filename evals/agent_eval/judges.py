"""声明式 Agent 行为与质量判定器套件（移植自 Piagent StructuredOutputJudge 与工具断言规范）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .harness import AgentRunResult


@dataclass
class JudgeVerdict:
    """单个判定器的评审结论。"""
    judge_name: str
    passed: bool
    score: float  # 0.0 ~ 1.0
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


class ToolExecutionJudge:
    """判定工具选择准确性、参数清洗质量与边界越权检测。"""

    def __init__(
        self,
        required_tools: Optional[List[str]] = None,
        forbidden_tools: Optional[List[str]] = None,
        min_data_count: int = 1,
    ) -> None:
        self.required_tools = required_tools or []
        self.forbidden_tools = forbidden_tools or []
        self.min_data_count = min_data_count

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        called_tools = [t["tool_name"] for t in result.tool_calls]
        reasons = []

        # 1. 检查禁止调用的工具（越界检查，如 web_agent 严禁调 search_amazon）
        for ft in self.forbidden_tools:
            if ft in called_tools:
                reasons.append(f"违规触发了受限工具: '{ft}'")

        # 2. 检查必须调用的工具（覆盖检查，如比价必须调 1688 和 Amazon）
        missing_tools = [rt for rt in self.required_tools if rt not in called_tools]
        if missing_tools:
            reasons.append(f"缺少必须调用的工具: {', '.join(missing_tools)}")

        # 3. 检查数据返回条数
        total_data = sum(t.get("data_count", 0) for t in result.tool_calls)
        if total_data < self.min_data_count and not reasons:
            reasons.append(f"工具有效数据条数不足 (期望 >= {self.min_data_count}, 实际 {total_data})")

        passed = len(reasons) == 0
        score = 1.0 if passed else (0.5 if not missing_tools and not any(ft in called_tools for ft in self.forbidden_tools) else 0.0)
        return JudgeVerdict(
            judge_name="ToolExecutionJudge",
            passed=passed,
            score=score,
            reason="；".join(reasons) if reasons else f"工具调用完全符合契约（实际调用 {len(called_tools)} 次）",
            details={"called_tools": called_tools, "total_data": total_data},
        )


class DeliverableJudge:
    """判定最终交付研报工件的结构完整性与规范。"""

    def __init__(self, required_sections: Optional[List[str]] = None, min_chars: int = 80) -> None:
        self.required_sections = required_sections or ["概况", "供应链", "建议"]
        self.min_chars = min_chars

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        report = result.report or ""
        reasons = []

        if len(report) < self.min_chars:
            reasons.append(f"研报字符数不足 (实际 {len(report)} < 期望 {self.min_chars})")

        missing_secs = [sec for sec in self.required_sections if not re.search(sec, report)]
        if missing_secs:
            reasons.append(f"研报缺失关键大纲小节: {', '.join(missing_secs)}")

        passed = len(reasons) == 0
        hit_ratio = (len(self.required_sections) - len(missing_secs)) / max(1, len(self.required_sections))
        score = 1.0 if passed else round(hit_ratio * 0.7, 2)

        return JudgeVerdict(
            judge_name="DeliverableJudge",
            passed=passed,
            score=score,
            reason="；".join(reasons) if reasons else f"研报工件完备，成功覆盖 {len(self.required_sections)} 个核心大纲章节",
            details={"report_chars": len(report), "missing_sections": missing_secs},
        )


class AntiHallucinationJudge:
    """判定 0 虚构与 0 假思考日志。"""

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        report = result.report or ""
        reasons = []

        fake_markers = ["思考\n总指挥", "运行工具\n规划电商调研大纲", "思考\n全景调研报告编制完成"]
        found_fakes = [m for m in fake_markers if m in report]
        if found_fakes:
            reasons.append(f"检测到虚假思考文案: {', '.join(found_fakes)}")

        passed = len(reasons) == 0
        return JudgeVerdict(
            judge_name="AntiHallucinationJudge",
            passed=passed,
            score=1.0 if passed else 0.0,
            reason="；".join(reasons) if reasons else "事实核验通过，0 假思考与伪造文案",
        )


class SteeringJudge:
    """判定运行期中途干预指令的采纳与动态重规划。"""

    def __init__(self, steering_keyword: str = "") -> None:
        self.steering_keyword = steering_keyword

    def evaluate(self, result: AgentRunResult) -> JudgeVerdict:
        report = result.report or ""
        reasons = []

        if not result.steering_applied or not self.steering_keyword:
            return JudgeVerdict("SteeringJudge", True, 1.0, "本用例未注入动态干预，自动放行")

        if self.steering_keyword not in report:
            reasons.append(f"研报未遵循中途动态干预关键诉求: '{self.steering_keyword}'")

        passed = len(reasons) == 0
        return JudgeVerdict(
            judge_name="SteeringJudge",
            passed=passed,
            score=1.0 if passed else 0.0,
            reason="；".join(reasons) if reasons else f"中途干预指令成功被主脑采纳并纠偏 ('{self.steering_keyword}')",
        )
