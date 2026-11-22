"""验证评测台账增量追加器：严禁覆盖已有记录，且完整记录技术栈与指标。"""
from __future__ import annotations

from pathlib import Path

from evals.history_log import append_agent_eval_record, append_rag_eval_record


def test_append_rag_eval_record_preserves_existing_content(tmp_path: Path) -> None:
    log_file = tmp_path / "测评记录.md"
    original = "# 历史评测台账\n\n## 里程碑一：Base\n- 保留的历史数据\n"
    log_file.write_text(original, encoding="utf-8")

    result = {
        "at": "2026-11-23 01:10:00",
        "git": "abc1234",
        "config": {"repeat": 1, "concurrency": 2, "corpus_scope": "all"},
        "retrieval": {"n": 112, "hit@1": 0.768, "hit@3": 0.875, "hit@8": 0.929, "recall@8": 0.888, "mrr": 0.828, "latency": {"p50_s": 4.03, "p95_s": 15.77}},
        "adversarial": {"n": 18, "pass_rate": 0.778, "canary_leaked": 0},
        "behavior": {"n": 177, "accuracy": 0.802, "relevant_rate": 0.904, "faithfulness_micro": 0.924},
        "six_metrics": {"sut_token_cost": {"total_tokens": 12345, "cost_usd": 0.0123}},
    }
    append_rag_eval_record(result, tmp_path / "report.json", log_path=log_file)
    content = log_file.read_text(encoding="utf-8")

    assert content.startswith(original)
    assert "Git `abc1234`" in content
    assert "编排与存储栈" in content
    assert "0.875" in content
    assert "80.2%" in content


def test_append_agent_eval_record_accumulates_multiple_runs(tmp_path: Path) -> None:
    log_file = tmp_path / "agent_测评记录.md"
    log_file.write_text("# Agent 历史记录\n", encoding="utf-8")

    base_report = {
        "timestamp": "20261123_011000",
        "run_id": "run_1",
        "mode": "synthetic_trace_regrade",
        "git_sha": "abc1234",
        "mean_score": 0.95,
        "gate": {"passed": True, "blocking_case_ids": []},
        "metrics": {
            "selected": 12,
            "passed": 11,
            "failed": 1,
            "unassessable": 0,
            "tool_selection_accuracy": {"rate": 0.92},
            "required_tool_coverage": {"rate": 1.0},
            "reference_fact_coverage": {"rate": 0.9},
        },
    }
    append_agent_eval_record(base_report, tmp_path / "r1.json", log_path=log_file)
    second_report = {**base_report, "run_id": "run_2", "mean_score": 1.0}
    append_agent_eval_record(second_report, tmp_path / "r2.json", log_path=log_file)

    content = log_file.read_text(encoding="utf-8")
    assert "# Agent 历史记录\n" in content
    assert "Run `run_1`" in content
    assert "Run `run_2`" in content
    assert "编排与存储栈" in content
