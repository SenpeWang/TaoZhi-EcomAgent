#!/usr/bin/env python3
"""V4 调研评测小集：速答、协调员工具、工具自主与审计角色。

V3 数据集保持只读；V4 通过 runner 的 ``--suite v4`` 独立运行。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evals" / "datasets" / "agent_v4_20261125"
VERSION = "agent_v4_20261125"


def _case(case_id: str, *, category: str, name: str, prompt: str, **extra) -> dict:
    """构造一条 V4 Agent 用例定义并推断能力大类。

    Args:
        case_id: 用例编号。
        category: 用例分类。
        name: 名称。
        prompt: 用例提问。
        **extra: 附加用例字段。

    Returns:
        用例定义字典。
    """
    return {
        "id": case_id,
        "capability": (
            "tool_use" if category in {"tool_correctness", "tool_call_accuracy"}
            else "planning_and_adaptation" if category == "plan_adherence"
            else "task_completion"
        ),
        "category": category,
        "track": "capability",
        "topic": category,
        "name": name,
        "prompt": prompt,
        "keyword": extra.pop("keyword", "测试品类"),
        "execution_mode": "synthetic_trace_regrade",
        "fixture_version": 1,
        "suite": "benchmark",
        "required_sections": [],
        "min_chars": 1,
        **extra,
    }


def _quick(number: int) -> tuple[dict, dict]:
    """构造速答路由用例及其观测。

    Args:
        number: 用例序号。

    Returns:
        (用例定义, 观测记录)。
    """
    cid = f"AGENT-V4-QUICK-{number:03d}"
    case = _case(cid, category="task_completion", name=f"速答路由 {number}", prompt=f"简单问题 {number}：解释一个术语。", expected_quick_answer=True)
    return case, {
        "case_id": cid,
        "fixture_version": 1,
        "system_status": "COMPLETED",
        "report": "这是一个简洁的速答结论。",
        "tool_calls": [],
        "evidence_ids": [],
        "subagent_roles": ["research.quick_answer"],
        "evidence_roles": ["research.quick_answer"],
    }


def _coord(number: int) -> tuple[dict, dict]:
    """构造协调员直接调用工具用例及其观测。

    Args:
        number: 用例序号。

    Returns:
        (用例定义, 观测记录)。
    """
    cid = f"AGENT-V4-COORD-{number:03d}"
    case = _case(
        cid,
        category="tool_correctness",
        name=f"协调员直接工具 {number}",
        prompt=f"读取当前时间（时区 {number}）。",
        expected_coordinator_tool=True,
        required_tools=["get_current_time"],
    )
    return case, {
        "case_id": cid,
        "fixture_version": 1,
        "system_status": "COMPLETED",
        "report": "当前时间已读取。",
        "tool_calls": [{
            "sequence": 1,
            "tool_name": "get_current_time",
            "parameters": {"timezone": "Asia/Shanghai"},
            "status": "ok",
            "data_count": 1,
            "dispatched": True,
            "items": [{"current_time": "2026-11-25 23:00", "timezone": "Asia/Shanghai"}],
        }],
        "evidence_ids": [f"V4-COORD-{number}"],
        "subagent_roles": [],
        "evidence_roles": ["research.orchestrator"],
    }


def _tool(number: int) -> tuple[dict, dict]:
    """构造工具自主选择用例及其观测。

    Args:
        number: 用例序号。

    Returns:
        (用例定义, 观测记录)。
    """
    cid = f"AGENT-V4-TOOL-{number:03d}"
    case = _case(
        cid,
        category="tool_call_accuracy",
        name=f"工具自主选择 {number}",
        prompt=f"检索全网关于测试品类 {number} 的口碑。",
        required_tools=["search_web"],
        forbidden_tools=["search_1688_wholesale", "search_amazon"],
    )
    return case, {
        "case_id": cid,
        "fixture_version": 1,
        "system_status": "COMPLETED",
        "report": f"测试品类 {number} 的口碑来自公开网页。",
        "tool_calls": [{
            "sequence": 1,
            "tool_name": "search_web",
            "parameters": {"query": f"测试品类 {number}"},
            "status": "ok",
            "data_count": 2,
            "dispatched": True,
            "items": [
                {"evidence_id": f"V4-WEB-{number}-1", "text": "公开网页摘要", "url": f"https://research.invalid/{number}/a"},
                {"evidence_id": f"V4-WEB-{number}-2", "text": "公开网页摘要二", "url": f"https://research.invalid/{number}/b"},
            ],
        }],
        "evidence_ids": [f"V4-WEB-{number}-1", f"V4-WEB-{number}-2"],
        "subagent_roles": ["research.market_intelligence"],
        "evidence_roles": ["research.market_intelligence"],
    }


def _audit(number: int) -> tuple[dict, dict]:
    """构造审计角色用例及其观测。

    Args:
        number: 用例序号。

    Returns:
        (用例定义, 观测记录)。
    """
    cid = f"AGENT-V4-AUDIT-{number:03d}"
    case = _case(
        cid,
        category="plan_adherence",
        name=f"审计角色 {number}",
        prompt=f"调研测试品类 {number} 的来源与缺口。",
        required_tools=["search_web"],
    )
    return case, {
        "case_id": cid,
        "fixture_version": 1,
        "system_status": "COMPLETED",
        "report": f"测试品类 {number} 的证据与缺口已记录。",
        "tool_calls": [{
            "sequence": 1,
            "tool_name": "search_web",
            "parameters": {"query": f"测试品类 {number}"},
            "status": "ok",
            "data_count": 1,
            "dispatched": True,
            "items": [{"evidence_id": f"V4-AUDIT-{number}", "text": "公开摘要", "url": f"https://research.invalid/{number}/audit"}],
        }],
        "evidence_ids": [f"V4-AUDIT-{number}"],
        "subagent_roles": ["research.generalist"],
        "evidence_roles": ["research.generalist"],
    }


def build() -> tuple[list[dict], list[dict]]:
    """按工厂函数批量构造 V4 用例与观测。

    Returns:
        (用例列表, 观测列表)。
    """
    cases, observations = [], []
    for index in range(1, 7):
        for factory in (_quick, _coord, _tool, _audit):
            case, observation = factory(index)
            cases.append(case)
            observations.append(observation)
    return cases, observations


def sha256(path: Path) -> str:
    """计算文件字节摘要。

    Args:
        path: 文件路径。

    Returns:
        十六进制 sha256。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """命令行入口：生成 V4 用例与观测并写盘。"""
    cases, observations = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "research_cases.json").write_text(
        json.dumps({"schema_version": 1, "source": "synthetic", "cases": cases}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "research_replay.json").write_text(
        json.dumps({"schema_version": 1, "source": "synthetic", "observations": observations}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "research_counterexamples.json").write_text(
        json.dumps({"schema_version": 1, "source": "synthetic", "counterexamples": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files = {name: sha256(OUT / name) for name in (
        "research_cases.json", "research_replay.json", "research_counterexamples.json",
    )}
    descriptor = {
        "version": VERSION,
        "created_at": "2026-11-25",
        "notes": "V4 小规模调研评测集：速答、协调员工具、工具自主与审计角色；与 V3 独立运行。",
        "suites": {
            "agent_research": {
                "eval_kind": "agent",
                "name": "agent_research",
                "description": "V4 小规模回归：24 例合成回放（速答/协调员工具/工具自主/审计角色）。",
                "case_count": len(cases),
                "content_sha256": hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                "catalog": "evals.agent_eval.cases:AGENT_V4_CASES",
                "files": files,
                "modes": ["live", "synthetic"],
                "scopes": ["full"],
            }
        },
    }
    (OUT / "dataset.json").write_text(json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# V4 Agent 小规模评测集（agent_v4_20261125）\n\n"
        "24 例合成回放，覆盖速答路由、协调员直接工具、工具自主选择与审计角色。\n\n"
        "| 文件 | 内容 |\n|---|---|\n"
        "| `research_cases.json` | 24 例 |\n"
        "| `research_replay.json` | 对应观测（含 `subagent_roles`/`evidence_roles`）|\n"
        "| `research_counterexamples.json` | 反例占位（V4 暂不生成）|\n\n"
        "运行：`python -m evals.agent_eval.runner --suite v4 --replay evals/datasets/agent_v4_20261125/research_replay.json`\n\n"
        "V3 数据集保持只读；V4 不影响 V3 默认回放。\n",
        encoding="utf-8",
    )
    print(f"generated {len(cases)} V4 cases at {OUT}")


if __name__ == "__main__":
    main()