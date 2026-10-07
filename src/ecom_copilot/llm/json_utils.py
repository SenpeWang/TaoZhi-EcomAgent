"""健壮的 JSON 解析工具。

背景：Sensenova / 部分国产 OpenAI 兼容端点不支持 function calling，
`response_format=json_object` 也常在输出外层包裹 ```json 代码块或思维链文本。
因此所有结构化输出统一走"提示词强约束 + 本地修复解析 + 重试"的链路。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Optional, Type

from pydantic import BaseModel, ValidationError

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def strip_fences(text: str) -> str:
    """去掉 markdown 代码块围栏，保留内容。"""
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        # 取最长的一个代码块，通常是真正的 JSON 结果
        return max(blocks, key=len).strip()
    return text.strip()


def _balanced_slice(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str, expect: str = "auto") -> Optional[Any]:
    """从任意 LLM 输出中尽力提取 JSON 对象 / 数组。"""
    if not text:
        return None
    candidates: List[str] = []

    stripped = strip_fences(text)
    if stripped and stripped != text.strip():
        candidates.append(stripped)

    # 优先取平衡括号片段
    for opener, closer in (("{", "}"), ("[", "]")):
        if expect in ("auto", "object") and opener == "{":
            seg = _balanced_slice(text, "{", "}")
            if seg:
                candidates.append(seg)
        if expect in ("auto", "array") and opener == "[":
            seg = _balanced_slice(text, "[", "]")
            if seg:
                candidates.append(seg)

    candidates.append(stripped)
    candidates.append(text.strip())

    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        parsed = _try_loads(cand)
        if parsed is not None:
            if expect == "object" and isinstance(parsed, dict):
                return parsed
            if expect == "array" and isinstance(parsed, list):
                return parsed
            if expect == "auto":
                return parsed
    return None


def _try_loads(text: str) -> Optional[Any]:
    for attempt in (text, _repair(text)):
        if not attempt:
            continue
        try:
            return json.loads(attempt)
        except Exception:
            continue
    return None


def _repair(text: str) -> str:
    """常见修复：去掉尾部逗号、把单引号换成双引号、去掉注释。"""
    s = text.strip()
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    # 单引号 key -> 双引号
    s = re.sub(r"(?m)^\s*'([^']+)'\s*:", r'"\1":', s)
    if s.count('"') % 2 == 1:  # 被截断的 JSON 末尾补引号
        s = s.rstrip().rstrip(",") + '"'
    # 补齐未闭合括号
    stack: List[str] = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    tail = "".join("}" if c == "{" else "]" for c in reversed(stack))
    return s + tail


def parse_model(text: str, model: Type[BaseModel], expect: str = "auto") -> Optional[BaseModel]:
    data = extract_json(text, expect=expect)
    if data is None:
        return None
    if isinstance(data, list) and model is not None:
        data = {"items": data} if _has_field(model, "items") else data
    try:
        return model.model_validate(data)
    except ValidationError:
        return _loose_validate(data, model)


def _has_field(model: Type[BaseModel], name: str) -> bool:
    return name in model.model_fields


def _loose_validate(data: Any, model: Type[BaseModel]) -> Optional[BaseModel]:
    """字段缺失/类型不符时做宽松兜底，保证流水线不中断。"""
    if not isinstance(data, dict):
        return None
    cleaned: dict = {}
    for name, field in model.model_fields.items():
        if name in data:
            cleaned[name] = data[name]
    for key, value in data.items():
        if key not in cleaned:
            cleaned[key] = value
    try:
        return model.model_validate(cleaned)
    except ValidationError:
        try:
            return model.model_construct(**cleaned)
        except Exception:
            return None


def to_json_schema_hint(model: Type[BaseModel]) -> str:
    """给 LLM 看的精简 schema 提示（避免冗长 JSON Schema 浪费 token）。"""
    lines: List[str] = []
    for name, field in model.model_fields.items():
        desc = field.description or ""
        lines.append(f'- "{name}": <{_type_name(field.annotation)}> {desc}'.rstrip())
    return "\n".join(lines)


def _type_name(annotation: Any) -> str:
    text = str(annotation)
    text = text.replace("typing.", "").replace("<class '", "").replace("'>", "")
    return text


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def iter_jsonl(path: str) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
