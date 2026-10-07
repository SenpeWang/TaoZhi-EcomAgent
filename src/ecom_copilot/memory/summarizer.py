"""会话历史超窗摘要压缩（Summarizer）与设计版 SUMMARIZE_PROMPT。

触发条件：注入前的历史 token 估算总量 > MEMORY_SUMMARY_THRESHOLD（默认 3000）。
压缩策略：较早轮次交给 LLM 按 SUMMARIZE_PROMPT 压缩为一条带 [历史摘要] 标注的消息，
保留最近 2 轮原文；LLM 不可用或调用失败时降级为「仅保留最近 K=4 轮」的截断策略。
任何异常都不得抛出主流程。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import Settings, get_settings
from ..llm import ModelTier, get_llm

SUMMARIZE_PROMPT = """你是电商客服助手的会话记忆压缩器。请把下面的多轮会话历史压缩为一段结构化摘要，用于后续轮次的上下文注入（不是给用户看的回复）。

【会话原文】
{transcript}

输出必须保留：
1. 用户身份、使用场景与偏好；
2. 已确认的事实结论（附来源文档名）；
3. 尚未解决的问题；
4. 用户明确纠正过的说法。
必须丢弃：寒暄、重复追问、检索过程性描述、被后续结论推翻的中间猜测。
输出 300 字以内中文，分四节：【偏好场景】【已确认结论】【未决问题】【用户纠错】。
"""

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def estimate_tokens(text: str) -> int:
    """启发式 token 估算（非精确分词器）：中文按 字数*0.6，英文按词数累加。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    words = len(_WORD_RE.findall(text))
    return int(cjk * 0.6 + words)


class Summarizer:
    """对话历史摘要器：超阈值时压缩旧轮次，保留最近原文。"""

    # 压缩成功后保留的最近原文轮数
    KEEP_ROUNDS = 2
    # 无 LLM / 压缩失败降级时保留的最近轮数
    FALLBACK_ROUNDS = 4

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def maybe_compress(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """超过 token 阈值时压缩；返回 [历史摘要消息] + 最近 2 轮原文，否则原样返回。"""
        try:
            if not history:
                return []
            if self._total_tokens(history) <= self.settings.memory_summary_threshold:
                return history
            kept, earlier = self._split_rounds(history, self.KEEP_ROUNDS)
            if not earlier:
                return history
            if not self.settings.has_llm:
                return self._truncate(history)
            summary = self._summarize(earlier)
            if not summary:
                return self._truncate(history)
            return [{"role": "assistant", "content": f"[历史摘要]\n{summary}"}] + kept
        except Exception:  # noqa: BLE001 — 摘要故障不得打断主流程
            return self._truncate(history)

    # ───────────────────────── 内部实现 ─────────────────────────
    def _total_tokens(self, history: List[Dict[str, Any]]) -> int:
        return sum(estimate_tokens(str(m.get("content", ""))) for m in history)

    @staticmethod
    def _split_rounds(history: List[Dict[str, Any]], keep: int
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """以 user 消息为轮次起点切分，返回 (最近 keep 轮原文, 更早轮次)。"""
        starts = [i for i, m in enumerate(history) if m.get("role") == "user"]
        if len(starts) <= keep:
            return list(history), []
        cut = starts[-keep]
        return history[cut:], history[:cut]

    def _truncate(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        kept, _ = self._split_rounds(history, self.FALLBACK_ROUNDS)
        return kept

    def _summarize(self, earlier: List[Dict[str, Any]]) -> str:
        transcript = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')}"
            for m in earlier
        )
        prompt = SUMMARIZE_PROMPT.format(transcript=transcript[-6000:])
        raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=800)
        return str(raw or "").strip()


__all__ = ["SUMMARIZE_PROMPT", "Summarizer", "estimate_tokens"]
