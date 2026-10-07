"""三级上下文压缩：microcompact / snip / collapse（继承自根因诊断 Agent）。

* microcompact —— 逐条裁剪：去掉空白、重复、低信息量尾巴；
* snip         —— 保留与当前查询最相关的句子，删除无关段落；
* collapse     —— 把多条证据折叠成结构化摘要，只保留论断 + 出处。
"""

from __future__ import annotations

import re
from typing import List, Sequence

from ..schemas.common import Evidence

_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")
_SENT_SPLIT = re.compile(r"(?<=[。！？；;!?])\s*")


def microcompact(text: str) -> str:
    """最轻量：压缩空白、去掉重复行与常见套话。"""
    if not text:
        return ""
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    lines = []
    seen = set()
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        lines.append(stripped)
    return "\n".join(lines)


def snip(text: str, query: str, max_chars: int = 1500) -> str:
    """按与查询的相关度保留句子。"""
    if not text:
        return ""
    if len(text) <= max_chars:
        return microcompact(text)
    terms = [t for t in re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z]{3,}", query) if len(t) >= 2]
    sentences = [s for s in _SENT_SPLIT.split(text) if s and s.strip()]
    scored = []
    for index, sentence in enumerate(sentences):
        score = sum(1 for t in terms if t in sentence)
        scored.append((score, index, sentence))
    scored.sort(key=lambda x: (-x[0], x[1]))
    budget = 0
    picked: List[tuple] = []
    for score, index, sentence in scored:
        if budget + len(sentence) > max_chars:
            continue
        picked.append((index, sentence))
        budget += len(sentence)
    picked.sort(key=lambda x: x[0])
    return microcompact("".join(s for _, s in picked))


def collapse(evidence: Sequence[Evidence], max_items: int = 8,
             quote_chars: int = 90) -> str:
    """把证据折叠成「论断 + 出处」的紧凑结构。"""
    if not evidence:
        return "（无可用证据）"
    items = sorted(evidence, key=lambda e: e.confidence, reverse=True)[:max_items]
    lines = []
    for index, item in enumerate(items, start=1):
        page = f"P{item.page}" if item.page else "全文"
        quote = item.quote[:quote_chars].replace("\n", " ")
        tag = "支持" if item.sentiment.value == "support" else (
            "反驳" if item.sentiment.value == "refute" else "中性")
        lines.append(f"{index}. [{tag}][{item.doc_name} {page}] {quote}")
    return "\n".join(lines)


def compress_context(text: str, query: str = "", max_chars: int = 8000,
                     level: str = "auto") -> str:
    """自动选择压缩等级。"""
    if not text:
        return ""
    if level == "microcompact" or len(text) <= max_chars:
        return microcompact(text)[:max_chars]
    if level == "collapse" or len(text) > max_chars * 3:
        return snip(microcompact(text), query, max_chars)
    return snip(text, query, max_chars)
