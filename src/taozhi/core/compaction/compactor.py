"""上下文压实引擎（Context Compaction Engine）。

自研上下文压缩治理架构：
1. 监控上下文窗口预算与 Token 占用；
2. 触发时将冗长历史转译为结构化的 <CONTEXT_SUMMARY> 架构块；
3. 保留高价值业务决策、用户需求序列、关键约束与下一步计划；
4. 剪枝庞大的工具输出与过程思考，保留 transcript_full 的行号指针；
5. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional

from ...ai.client import LLMClient, ModelTier
from ..memory.transcript import TranscriptLogger


class ContextCompactor:
    """负责上下文窗口的预算监控、智能裁剪与结构化压实。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        max_char_threshold: int = 50000,
        trigger_char_threshold: int = 35000,
    ) -> None:
        """初始化上下文压实器。

        Args:
            llm_client: LLM 客户端实例。
            max_char_threshold: 允许的最大硬上限字符数。
            trigger_char_threshold: 触发压实的软上限阈值。
        """
        self.llm = llm_client or LLMClient()
        self.max_char_threshold = max_char_threshold
        self.trigger_char_threshold = trigger_char_threshold

    def estimate_tokens(self, text: str) -> int:
        """粗略估算文本的 Token 消耗（中英文混合场景）。

        Args:
            text: 输入文本。

        Returns:
            int: 估算的 Token 数量。
        """
        # 中文约 1 token / 1.5 char，英文约 1 token / 4 char，综合按 1:2.5 估算
        return max(1, len(text) // 2)

    def should_compact(self, transcript_steps: List[Dict[str, Any]]) -> bool:
        """判断当前会话轨迹是否达到了压实触发阈值。

        Args:
            transcript_steps: 轨迹步骤字典列表。

        Returns:
            bool: True 表示需要执行压实，False 表示在健康预算内。
        """
        total_len = sum(
            len(s.get("content", "")) + len(s.get("thinking", ""))
            for s in transcript_steps
        )
        return total_len >= self.trigger_char_threshold

    def build_summary_prompt(self, steps: List[Dict[str, Any]]) -> str:
        """构造供 LLM 提炼高保真上下文摘要的 Prompt。

        Args:
            steps: 待压实的完整或紧凑历史步骤。

        Returns:
            str: 格式化的提炼 Prompt。
        """
        history_text_parts = []
        for s in steps:
            idx = s.get("step_index", 0)
            src = s.get("source", "UNKNOWN")
            stype = s.get("type", "UNKNOWN")
            content = s.get("content", "")
            history_text_parts.append(f"[Step {idx} | {src} | {stype}]:\n{content}")

        history_blob = "\n\n".join(history_text_parts)
        return (
            "请阅读以下对话与任务执行历史，并输出一份高度精炼、忠于事实的《上下文压实快照（Context Summary）》。\n\n"
            "【历史记录】：\n"
            f"{history_blob}\n\n"
            "【必须包含以下结构化小节】：\n"
            "1. # User Requests：按时间顺序列举用户的核心诉求与变更；\n"
            "2. # Previous Session Summary：\n"
            "   - Task Overview：任务总览；\n"
            "   - Scope Boundaries & Constraints：严格约束与规则边界；\n"
            "   - Progress & Completed Milestones：已完成的工作与交付物；\n"
            "   - Key Findings：核心调研与计算事实结论；\n"
            "   - Errors & Fixes：过程中遇到的故障及修复方案；\n"
            "   - Active Context：当前活跃的变量、文件指针与工作区状态；\n"
            "   - Next Steps：后续明确的下一步行动清单。\n"
            "【原则】：绝对忠于已有历史事实，严禁幻觉虚构，信息密度最大化。"
        )

    def compact(
        self,
        transcript_logger: TranscriptLogger,
        active_subagents: Optional[List[Dict[str, Any]]] = None,
        on_compact_done: Optional[Callable[[str], None]] = None,
    ) -> str:
        """执行全量压实流程，生成紧凑的 <CONTEXT_SUMMARY> 架构块。

        Args:
            transcript_logger: 关联的双轨日志管理器。
            active_subagents: 当前处于运行中的后台子智能体列表。
            on_compact_done: 压实完成回调通知。

        Returns:
            str: 格式化后的 <CONTEXT_SUMMARY> 块。
        """
        compact_steps = transcript_logger.read_compact_steps()
        if not compact_steps:
            return "<CONTEXT_SUMMARY>\n当前尚无历史记录。\n</CONTEXT_SUMMARY>"

        prompt = self.build_summary_prompt(compact_steps)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 调用 FAST 或 STRONG 模型提炼高密度摘要
        summary_content = self.llm.chat(
            prompt,
            tier=ModelTier.FAST,
            system="你是一名资深的上下文架构与压缩引擎，专门提取密集事实快照。",
            max_tokens=3000,
        )

        subagent_block = ""
        if active_subagents:
            subagent_lines = [
                f"- ID: {sa.get('id')}, Role: {sa.get('role')}, Status: {sa.get('status')}"
                for sa in active_subagents
            ]
            subagent_block = (
                "\n\n# Running Background Subagents\n"
                + "\n".join(subagent_lines)
            )

        compact_result = (
            "<CONTEXT_SUMMARY>\n"
            f"This summary was generated at {now_iso}.\n\n"
            f"{summary_content}{subagent_block}\n\n"
            "# Conversation Logs\n"
            f"- Compact: {transcript_logger.compact_path}\n"
            f"- Full: {transcript_logger.full_path}\n"
            "- Start with compact transcript; when `truncated_fields` is present, read only that specific line in `transcript_full.jsonl`.\n"
            "</CONTEXT_SUMMARY>"
        )

        # 在日志中追加一条 COMPACT 记录
        transcript_logger.append_step(
            source="SYSTEM",
            step_type="CONTEXT_COMPACT",
            content=f"Context successfully compacted ({len(compact_steps)} steps compressed).",
            metadata={"generated_at": now_iso, "summary_len": len(compact_result)},
        )

        if on_compact_done:
            on_compact_done(compact_result)

        return compact_result
