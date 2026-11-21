"""纯文本注入防护与清洗规则（零外部依赖领域内核）。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, List, Tuple

_INJECTION_PATTERNS: List[Tuple[str, str]] = [
    (r"ignore\s+(all\s+)?previous\s+instructions", "英文越权指令"),
    (r"忽略(上面|以上|之前)(的)?(所有)?(指令|提示|规则|约束)", "中文越权指令"),
    (r"忘记(你|之前|上面)(的)?(所有)?(设定|指令|身份)", "身份重置攻击"),
    (r"你现在(是|扮演|作为).{0,20}(?:系统|管理员|root|developer)", "角色扮演越权"),
    (r"(system|assistant)\s*:\s*", "伪造对话角色"),
    (r"<\s*/?\s*(?:system|assistant|tool_call|context|prompt|instruction)\b[^>]*>", "伪造结构化标记逃逸"),
    (r"\[\s*/?\s*(?:SYSTEM|ASSISTANT|TOOL_CALL|INST)\s*\]", "伪造指令分隔符"),
    (r"(reveal|print|输出).{0,15}(system prompt|提示词|密钥|api[_ -]?key)", "敏感信息泄露探测"),
    (r"sudo\s+rm|rm\s+-rf|drop\s+table|delete\s+from", "危险操作指令"),
    (r"(curl|wget)\s+http", "外联指令"),
    (r"base64[,:]\s*[A-Za-z0-9+/=]{60,}", "编码载荷"),
    (r"DAN|jailbreak|开发者模式", "越狱关键词"),
]

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_MAX_INPUT_CHARS = 4000
_INVALID_JSON_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")


def strip_invalid_text(value: Any) -> Any:
    """递归清理无法写入 JSON/JSONB 的字符并返回同结构副本。

    PostgreSQL 的 JSONB 拒绝 NUL（``\\u0000``）与孤立代理项，模型或抓取结果一旦携带
    这些字符就会导致整条记录写入失败，因此在持久化边界统一清洗。

    Args:
        value: 待写入 JSONB 的任意 Python 对象（字符串、字典、列表或标量）。

    Returns:
        清理后的同结构对象：去除 NUL、非法控制字符与孤立代理项。
    """
    if isinstance(value, str):
        safe = value.encode("utf-8", "ignore").decode("utf-8", "ignore")
        return _INVALID_JSON_CHARS_RE.sub("", safe)
    if isinstance(value, dict):
        return {strip_invalid_text(k): strip_invalid_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [strip_invalid_text(item) for item in value]
    return value


def sanitize(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """去除控制字符、零宽走私字符、统一全角并截断超长输入。"""
    if text is None:
        return ""
    text = _ZERO_WIDTH_RE.sub("", str(text))
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_RE.sub(" ", text)
    text = re.sub(r"\s{3,}", "  ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + " …[已截断]"
    return text


def detect_injection(text: str) -> Tuple[bool, List[str]]:
    """探测 Prompt 注入与混淆走私攻击，返回 (是否可疑, 命中规则列表)。"""
    if not text:
        return False, []
    hits: List[str] = []
    raw_probe = text[:8000]
    if len(_ZERO_WIDTH_RE.findall(raw_probe)) >= 3:
        hits.append("零宽走私字符载荷")

    clean_probe = _ZERO_WIDTH_RE.sub("", raw_probe)
    probe = unicodedata.normalize("NFKC", clean_probe)
    for pattern, label in _INJECTION_PATTERNS:
        if re.search(pattern, probe, flags=re.IGNORECASE):
            hits.append(label)
    if len(text) > 6000 and len(set(text)) / max(1, len(text)) < 0.02:
        hits.append("异常重复载荷")
    return (len(hits) > 0), hits
