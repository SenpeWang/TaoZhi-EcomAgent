"""MCP 工具参数契约：按 JSON Schema 补齐默认值并校验调用参数。

工具描述、参数默认值与取值范围只允许在 MCP 服务端的工具定义中维护，
适配器与执行端共用本模块，避免参数规则在多层重复。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: type(value) is int,
    "number": lambda value: type(value) in (int, float),
    "boolean": lambda value: type(value) is bool,
}


def property_names(schema: Mapping[str, Any]) -> set[str]:
    """返回 schema 声明的全部属性名。"""
    return set((schema.get("properties") or {}).keys())


def required_names(schema: Mapping[str, Any]) -> set[str]:
    """返回 schema 声明的必填属性名。"""
    return set(schema.get("required") or ())


def apply_defaults(schema: Mapping[str, Any], arguments: Mapping[str, Any] | None) -> Dict[str, Any]:
    """按 schema 的 ``default`` 补齐缺省参数，返回新字典。"""
    result = dict(arguments or {})
    for name, spec in (schema.get("properties") or {}).items():
        if name not in result and isinstance(spec, dict) and "default" in spec:
            result[name] = spec["default"]
    return result


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """校验必填、额外字段、类型、枚举与数值/长度边界。

    Raises:
        ValueError: 参数不符合 schema 时抛出，调用方统一转换为 ``tool.invalid_arguments``。
    """
    properties = schema.get("properties") or {}
    actual = dict(arguments or {})
    unknown = set(actual) - set(properties)
    if unknown:
        raise ValueError("存在未声明的参数")
    if not required_names(schema) <= set(actual):
        raise ValueError("缺少必填参数")
    for name, value in actual.items():
        _validate_value(name, properties.get(name) or {}, value)


def _validate_value(name: str, spec: Mapping[str, Any], value: Any) -> None:
    """校验单个属性值；只支持工具契约实际使用的 JSON Schema 子集。"""
    expected = spec.get("type")
    if expected in _TYPE_CHECKS and not _TYPE_CHECKS[expected](value):
        raise ValueError(f"参数 {name} 类型不正确")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"参数 {name} 不在允许范围")
    if expected in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"]:
            raise ValueError(f"参数 {name} 小于最小值")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValueError(f"参数 {name} 大于最大值")
    if expected == "string":
        if "minLength" in spec and len(value) < spec["minLength"]:
            raise ValueError(f"参数 {name} 过短")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            raise ValueError(f"参数 {name} 过长")


def schema_defaults(schema: Mapping[str, Any], names: Iterable[str] | None = None) -> Dict[str, Any]:
    """读取 schema 中指定属性的默认值，供适配器与契约测试共用。"""
    selected = set(names) if names is not None else set(property_names(schema))
    return {
        name: spec["default"]
        for name, spec in (schema.get("properties") or {}).items()
        if name in selected and isinstance(spec, dict) and "default" in spec
    }