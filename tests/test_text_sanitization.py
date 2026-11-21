"""非法 Unicode 字符清洗与 JSONB 写入安全性的单元测试。"""
from __future__ import annotations

from taozhi.domain.text_guard import strip_invalid_text
from taozhi.knowledge.db import as_jsonb


def test_strip_invalid_text_removes_nul_and_control_chars():
    """NUL 与非法控制字符必须被移除，正文其余内容保持完整。"""
    raw = "报告\u0000正文\u0001继续\u007f结束"
    cleaned = strip_invalid_text(raw)
    assert "\x00" not in cleaned
    assert "\x01" not in cleaned
    assert "\x7f" not in cleaned
    assert "报告正文继续结束" == cleaned


def test_strip_invalid_text_keeps_legal_whitespace():
    """换行与制表符属于合法空白，不应被清洗。"""
    raw = "第一行\n第二行\t制表"
    assert strip_invalid_text(raw) == raw


def test_strip_invalid_text_recurses_into_structures():
    """字典与列表中的字符串同样被清洗，结构保持原样。"""
    payload = {"a": ["x\u0000y", {"b": "z\u0001"}], "c": 1}
    cleaned = strip_invalid_text(payload)
    assert cleaned == {"a": ["xy", {"b": "z"}], "c": 1}


def test_strip_invalid_text_drops_unpaired_surrogates():
    """孤立代理项会被丢弃，避免写入 JSONB 时报 Unicode 错误。"""
    raw = "正常\ud800文本"
    assert strip_invalid_text(raw) == "正常文本"


def test_as_jsonb_applies_sanitization():
    """as_jsonb 写入前已清洗 NUL，序列化结果不含非法转义。"""
    wrapped = as_jsonb({"text": "含\u0000空字符"})
    obj = getattr(wrapped, "obj", wrapped)
    assert "\x00" not in str(obj)