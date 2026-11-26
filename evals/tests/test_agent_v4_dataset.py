"""V4 调研评测集契约：与 V3 隔离、角色回放、描述符完整。"""
from __future__ import annotations

import json
from pathlib import Path

from evals.agent_eval.cases import AGENT_BENCHMARK_CASES, AGENT_V4_CASES
from evals.agent_eval.contracts import validate_cases

ROOT = Path(__file__).resolve().parents[2]
V4 = ROOT / "evals" / "datasets" / "agent_v4_20261125"


def test_v4_cases_and_replay_are_aligned():
    """V4 用例可校验，回放与用例一一对应，且带角色字段。"""
    validate_cases(AGENT_V4_CASES)
    assert len(AGENT_V4_CASES) == 24
    replay = json.loads((V4 / "research_replay.json").read_text(encoding="utf-8"))
    observations = {item["case_id"]: item for item in replay["observations"]}
    assert set(observations) == {case["id"] for case in AGENT_V4_CASES}
    assert all(isinstance(item.get("subagent_roles"), list) for item in observations.values())
    assert all(isinstance(item.get("evidence_roles"), list) for item in observations.values())


def test_v4_does_not_change_v3_default_catalog():
    """V3 默认目录保持冻结，V4 只通过显式 suite 选择。"""
    assert len(AGENT_BENCHMARK_CASES) == 610
    assert all(case["id"].startswith("AGENT-V3") or case["id"].startswith("AGENT-") for case in AGENT_BENCHMARK_CASES)
    assert not any(case["id"].startswith("AGENT-V4") for case in AGENT_BENCHMARK_CASES)


def test_v4_descriptor_registers_files():
    """V4 描述符必须登记版本、用例数与文件摘要。"""
    descriptor = json.loads((V4 / "dataset.json").read_text(encoding="utf-8"))
    suite = descriptor["suites"]["agent_research"]
    assert descriptor["version"] == "agent_v4_20261125"
    assert suite["case_count"] == 24
    assert set(suite["files"]) == {
        "research_cases.json", "research_replay.json", "research_counterexamples.json"
    }