"""V3 数据集、命名登记与统计口径的一致性校验。"""
import hashlib
import json
from pathlib import Path
import re

from evals import naming
from evals.agent_eval.cases import AGENT_BENCHMARK_CASES
from evals.agent_eval.contracts import validate_cases as validate_agent_cases
from evals.agent_eval.reporting import fingerprint
from evals.dataset import schema as rag_schema
from evals.metrics import pass_at_k, pass_pow_k_estimate, wilson_interval

ROOT = Path(__file__).resolve().parents[2]
V3 = ROOT / "evals/datasets/v3_20261124"
TRACK_COUNTS = {"golden": 30, "capability": 20, "regression": 15, "challenge": 10, "heldout": 15}
ANTHROPIC_LINKS = (
    "https://www.anthropic.com/research/demystifying-evals-for-ai-agents",
    "https://www.anthropic.com/research/bloom",
    "https://www.anthropic.com/research/AI-resistant-technical-evaluations",
    "https://www.anthropic.com/research/infrastructure-noise",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_descriptor_registers_v3_suites_and_files():
    descriptor = json.loads((V3 / "dataset.json").read_text(encoding="utf-8"))
    assert descriptor["version"] == "v3_20261124"
    suites = descriptor["suites"]
    assert set(suites) == {"agent_research", "rag_core", "rag_adversarial"}
    assert suites["rag_core"]["case_count"] == 540
    assert suites["rag_adversarial"]["case_count"] == 60
    assert suites["agent_research"]["case_count"] == len(AGENT_BENCHMARK_CASES) == 610
    for suite, definition in suites.items():
        for relative, expected in definition["files"].items():
            assert sha256(V3 / relative) == expected, f"{suite} 文件摘要不一致：{relative}"


def test_rag_v3_counts_and_contracts():
    cases = [json.loads(line) for line in (V3 / "rag/cases.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    assert len(cases) == 540
    assert rag_schema.validate_cases(cases) == []
    for capability in naming.RAG_CAPABILITIES:
        for track, expected in TRACK_COUNTS.items():
            actual = sum(1 for case in cases
                         if case["capability"] == capability and case["track"] == track)
            assert actual == expected, f"{capability}/{track} 期望 {expected}，实际 {actual}"
    categories = {(case["capability"], case["category"]) for case in cases}
    for capability, subs in naming.RAG_SUBCATEGORIES.items():
        assert {sub for cap, sub in categories if cap == capability} == set(subs)
    documents = [json.loads(line) for line in
                 (V3 / "rag/corpus_docs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(documents) >= 80
    doc_ids = {doc["doc_id"] for doc in documents}
    for case in cases:
        assert set(case["expected_documents"]) <= doc_ids


def test_rag_redteam_contract():
    cases = json.loads((V3 / "rag/redteam.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 60
    for case in cases:
        assert case["track"] == "redteam"
        assert case["capability"] in naming.RAG_CAPABILITIES
        assert case["category"] in naming.RAG_SUBCATEGORIES[case["capability"]]
        assert case["basis"].strip()
        judge = case["judge"]
        assert judge["mode"] in ("decline", "facts")
        for pattern in judge.get("must_match", []) + judge.get("must_not_match", []):
            re.compile(pattern)
        if judge["mode"] == "decline":
            assert judge.get("must_contain_any")


def test_agent_v3_counts_and_contracts():
    cases = json.loads((V3 / "agent/research_cases.json").read_text(encoding="utf-8"))["cases"]
    observations = json.loads((V3 / "agent/research_replay.json").read_text(encoding="utf-8"))["observations"]
    counterexamples = json.loads(
        (V3 / "agent/research_counterexamples.json").read_text(encoding="utf-8"))["counterexamples"]
    assert len(cases) == len(observations) == len(counterexamples) == 600
    validate_agent_cases(cases)
    for capability in naming.AGENT_CAPABILITIES:
        for track, expected in TRACK_COUNTS.items():
            actual = sum(1 for case in cases
                         if case["capability"] == capability and case["track"] == track)
            assert actual == expected, f"{capability}/{track} 期望 {expected}，实际 {actual}"
    redteam = [case for case in cases if case["track"] == "redteam"]
    assert len(redteam) == 60
    assert {case["capability"] for case in redteam} == {"security_and_governance"}
    assert {case["category"] for case in cases} == {
        sub for subs in naming.AGENT_SUBCATEGORIES.values() for sub in subs}
    descriptor = json.loads((V3 / "dataset.json").read_text(encoding="utf-8"))
    assert descriptor["suites"]["agent_research"]["content_sha256"] == fingerprint(
        sorted(AGENT_BENCHMARK_CASES, key=lambda case: case["id"]))


def test_readmes_carry_sources_and_naming_registry():
    for relative, capabilities, subcategories in (
            ("rag/README.md", naming.RAG_CAPABILITIES, naming.RAG_SUBCATEGORIES),
            ("agent/README.md", naming.AGENT_CAPABILITIES, naming.AGENT_SUBCATEGORIES)):
        text = (V3 / relative).read_text(encoding="utf-8")
        assert "参考来源与依据" in text
        assert "子类命名登记表" in text
        for link in ANTHROPIC_LINKS:
            assert link in text, f"{relative} 缺少来源：{link}"
        for capability in capabilities:
            assert f"`{capability}`" in text
            for subcategory in subcategories[capability]:
                assert f"`{subcategory}`" in text, f"{relative} 缺少子类：{subcategory}"
        assert "synthetic" in text.lower() or "合成" in text
        assert "未经人工校准" in text


def test_statistics_helpers():
    assert pass_at_k([1], [1], 1) == 1.0
    assert pass_at_k([0], [1], 1) == 0.0
    assert pass_pow_k_estimate([1, 0], [1, 1], 1) == 0.5
    low, high = wilson_interval(9, 10)
    assert 0.0 <= low < 0.9 < high <= 1.0
    assert wilson_interval(0, 0) is None


def test_rag_v3_generator_snapshot_matches_committed_files():
    """generate_v3 --check 必须与仓库快照逐字节一致（spec/生成器漂移即失败）。"""
    import os
    import subprocess
    import sys

    pythonpath = os.pathsep.join(
        [str(ROOT), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run([sys.executable, "-m", "evals.dataset.generate_v3", "--check"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "[OK]" in result.stdout


def test_agent_reliability_pass_at_k_and_pass_pow_k():
    """trials 汇总：pass@1 / pass@k / pass^k 必须按 trial 结果计算。"""
    from evals.agent_eval.runner import _reliability

    summary = _reliability({"a": [True, True], "b": [True, False], "c": [False, False]}, 2)
    assert summary["trials"] == 2
    assert summary["cases"] == 3
    assert summary["pass_at_1"] == 0.5
    assert summary["pass_at_k"] == round((1.0 + 1.0 + 0.0) / 3, 4)
    assert summary["pass_pow_k"] == round((1.0 + 0.0 + 0.0) / 3, 4)
    assert summary["cases_passed_once"] == 2
    assert summary["cases_passed_all"] == 1


def test_rag_heldout_is_excluded_unless_requested(monkeypatch):
    """heldout 默认不进入日常报告，显式开关才纳入发布前复核。"""
    from evals.rag_eval import run as rag_run

    monkeypatch.setattr(rag_run, "INCLUDE_HELDOUT", False)
    assert rag_run._eligible({"track": "heldout"}) is False
    assert rag_run._eligible({"track": "golden"}) is True
    monkeypatch.setattr(rag_run, "INCLUDE_HELDOUT", True)
    assert rag_run._eligible({"track": "heldout"}) is True


def test_agent_v3_generator_snapshot_matches_committed_files():
    """agent generate --version v3 --check 必须与仓库快照逐字节一致。"""
    import os
    import subprocess
    import sys

    pythonpath = os.pathsep.join(
        [str(ROOT), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [sys.executable, "-m", "evals.agent_eval.generate", "--version", "v3",
         "--out", str(V3 / "agent"), "--check"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "[OK]" in result.stdout


def test_readme_covers_every_registered_source_link():
    """README 必须登记命名表里用到的每个来源链接（不允许来源只存在于代码）。"""
    for relative, subcategories in (("rag/README.md", naming.RAG_SUBCATEGORIES),
                                    ("agent/README.md", naming.AGENT_SUBCATEGORIES)):
        readme = (V3 / relative).read_text(encoding="utf-8")
        for entries in subcategories.values():
            for _sub, (source, _state, _v2) in entries.items():
                _name, url = naming.SOURCES[source]
                assert url in readme, f"{relative} 缺少来源链接：{url}"


def test_agent_heldout_is_excluded_by_default():
    """Agent 日常回放默认排除 heldout；只有显式 include 才纳入。"""
    from evals.agent_eval.runner import filter_heldout

    cases = [{"id": "A", "track": "golden"}, {"id": "B", "track": "heldout"}]
    assert [case["id"] for case in filter_heldout(cases, False)] == ["A"]
    assert [case["id"] for case in filter_heldout(cases, True)] == ["A", "B"]
