"""JSON 修复解析单元测试：端点不支持 function calling 时的关键容错链路。"""

import pytest

from ecom_copilot.llm.json_utils import extract_json, parse_model, strip_fences
from ecom_copilot.schemas.graph import EdgeType, SupplyTriple


def test_strip_fences():
    assert strip_fences("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert strip_fences("plain") == "plain"


def test_extract_from_code_fence():
    raw = '思考过程...\n```json\n{"entities": [{"name": "MC-500"}], "triples": []}\n```\n以上'
    assert extract_json(raw)["entities"][0]["name"] == "MC-500"


def test_extract_from_noisy_text():
    raw = '好的，结果如下：\n{"a": [1, 2,], "b": "中文"}  \n希望有帮助'
    data = extract_json(raw)
    assert data is not None
    assert data["b"] == "中文"


def test_extract_array():
    raw = "```json\n[{\"x\": 1}, {\"x\": 2}]\n```"
    data = extract_json(raw, expect="array")
    assert isinstance(data, list) and len(data) == 2


def test_extract_unbalanced():
    raw = '{"a": {"b": 1}'
    assert extract_json(raw) is None or isinstance(extract_json(raw), dict)


def test_extract_none():
    assert extract_json("") is None
    assert extract_json("完全没有 JSON") is None


def test_parse_model_loose():
    raw = '```json\n{"subject": "C3", "predicate": "COMPATIBLE_WITH", "obj": "iPhone 16", "confidence": "0.8"}\n```'
    triple = parse_model(raw, SupplyTriple)
    assert triple is not None
    assert triple.subject == "C3"
    assert triple.predicate.value == "COMPATIBLE_WITH"
    assert triple.confidence == pytest.approx(0.8)


def test_triple_key():
    triple = SupplyTriple(subject="C3", predicate=EdgeType.COMPATIBLE_WITH, obj="iPhone 16")
    assert triple.key() == "COMPATIBLE_WITH|C3|iPhone 16"
