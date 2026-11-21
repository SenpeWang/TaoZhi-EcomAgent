"""根据版本化的单项替换重建调研反例，保留原始合成记录。"""
from __future__ import annotations

import copy


def apply_counterexample(observation: dict, variant: dict) -> dict:
    """按同一用例及夹具版本替换既有观测值，可用于原始记录与反例配对比较。"""
    if not isinstance(observation, dict) or not isinstance(variant, dict):
        raise ValueError("反例需提供合成观测及变体对象")
    if not isinstance(variant.get("case_id"), str) or variant["case_id"] != observation.get("case_id"):
        raise ValueError("反例与观测用例编号不一致")
    version = variant.get("fixture_version")
    if type(version) is not int or type(observation.get("fixture_version")) is not int or version != observation["fixture_version"]:
        raise ValueError("反例与观测的夹具版本不一致")
    edit = variant.get("edit")
    if not isinstance(edit, dict) or edit.keys() != {"path", "value"}:
        raise ValueError("反例须指定唯一替换路径和值")
    path = edit["path"]
    if not isinstance(path, list) or not path or path[0] not in ("report", "tool_calls", "evidence_ids", "steering_after_sequence", "steering_applied", "system_status"):
        raise ValueError("反例只允许替换业务观测字段")
    result = copy.deepcopy(observation)
    parent = result
    for position, key in enumerate(path):
        if isinstance(parent, list):
            valid = type(key) is int and 0 <= key < len(parent)
        else:
            valid = isinstance(parent, dict) and isinstance(key, str) and key in parent
        if not valid:
            raise ValueError("反例的替换路径不在原始记录中")
        if position == len(path) - 1:
            if parent[key] == edit["value"]:
                raise ValueError("反例替换必须改变原始观测")
            parent[key] = copy.deepcopy(edit["value"])
        else:
            parent = parent[key]
    return result
