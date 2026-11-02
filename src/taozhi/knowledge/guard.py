"""数据采集安全护栏：Prompt 注入检测 + 子 Agent 工具白名单。

继承自工行生产经验：输入白名单校验 + 模型层过滤 + 只读工具收敛。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

# ───────────────────── Prompt 注入检测 ─────────────────────

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


def sanitize(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """去除控制字符、零宽走私字符、统一全角、截断超长输入。"""
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
    """返回 (是否可疑, 命中规则列表)。"""
    if not text:
        return False, []
    hits: List[str] = []
    # 探测异常隐形走私字符
    raw_probe = text[:8000]
    zw_matches = len(_ZERO_WIDTH_RE.findall(raw_probe))
    if zw_matches >= 3:
        hits.append("零宽走私字符载荷")

    # 剥离隐形走私字符并 NFKC 归一化，彻底瓦解 "i\u200bg\u200bn\u200bo\u200br\u200be" 等字符穿透混淆
    clean_probe = _ZERO_WIDTH_RE.sub("", raw_probe)
    probe = unicodedata.normalize("NFKC", clean_probe)
    for pattern, label in _INJECTION_PATTERNS:
        if re.search(pattern, probe, flags=re.IGNORECASE):
            hits.append(label)
    if len(text) > 6000 and len(set(text)) / max(1, len(text)) < 0.02:
        hits.append("异常重复载荷")
    return (len(hits) > 0), hits


def is_safe(text: str) -> bool:
    return not detect_injection(text)[0]


# ───────────────────── 子 Agent 工具白名单 ─────────────────────

# 只读工具：spawn 出去的数据采集子 Agent 只能使用这些能力
READ_ONLY_TOOLS = {
    "fetch_source",        # 抓取指定数据源
    "search_corpus",       # 检索本地/知识库语料
    "vector_search",       # 向量召回
    "bm25_search",         # 关键词召回
    "graph_query",         # 图谱只读查询
    "read_document",       # 读取已入库文档
}

# 高危工具：子 Agent 永远不被授予
FORBIDDEN_TOOLS = {
    "write_graph", "delete_document", "execute_sql", "shell",
    "http_post", "send_email", "update_permission",
}


class ToolWhitelistError(PermissionError):
    pass


def assert_tool_allowed(tool_name: str) -> None:
    if tool_name in FORBIDDEN_TOOLS:
        raise ToolWhitelistError(f"工具 {tool_name} 属于高危工具，禁止授予子 Agent")
    if tool_name not in READ_ONLY_TOOLS:
        raise ToolWhitelistError(f"工具 {tool_name} 不在子 Agent 只读白名单内")


def allowed_tools() -> List[str]:
    return sorted(READ_ONLY_TOOLS)
