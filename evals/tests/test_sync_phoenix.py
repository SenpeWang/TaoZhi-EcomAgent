#!/usr/bin/env python3
"""验证 Phoenix 导出的逐例指标和幂等写入。"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock

import pytest

from evals.agent_eval.contracts import REPORT_SCHEMA_VERSION
from evals.sync_phoenix import (
    _log_case, apply_behavior, apply_retrieval, case_to_example, collect_results,
    experiment_name, sync_agent_eval,
)


@pytest.mark.parametrize(("version", "mode"), [(REPORT_SCHEMA_VERSION - 1, "live"),
                                                (REPORT_SCHEMA_VERSION, "synthetic_trace_regrade")])
def test_agent_export_rejects_unsupported_reports_before_writing(tmp_path, version, mode):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema_version": version, "mode": mode}))
    client = Mock()
    with pytest.raises(ValueError, match="当前格式的真实调研报告"):
        sync_agent_eval(client, path, "test", False, False)
    assert not client.mock_calls


def test_agent_export_resumes_missing_annotations_without_creating_runs(tmp_path):
    class Dataset(list):
        id = "dataset"

    case_id = "AGENT-WEB-003"
    case = {"case_id": case_id, "name": "网页调研", "status": "unassessable", "score": None, "elapsed": None,
            "tool_calls_count": None,
            "verdicts": [{"judge": "Observation", "status": "unassessable", "reason": "无观测", "score": None}]}
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema_version": REPORT_SCHEMA_VERSION, "mode": "live", "run_id": "batch",
                                "git_sha": None, "dataset_sha256": "a" * 64, "judges_sha256": "b" * 64,
                                "metrics": {"selected": 1, "task_completion": {"rate": None}}, "case_results": [case]}))
    client = Mock()
    client.datasets.get_dataset.return_value = Dataset([{"metadata": {"case_id": case_id}, "node_id": "example"}])
    client.experiments.list.return_value = [{"name": "agent-batch", "id": "exp"}]
    client.experiments.get_experiment.return_value = {"task_runs": [{"dataset_example_id": "example", "id": "run"}]}
    sync_agent_eval(client, path, "test", False, False)
    client.experiments.create.assert_not_called()
    client.experiments.log_run.assert_not_called()
    annotations = [call.kwargs for call in client.experiments.log_evaluation.call_args_list]
    assert [row["name"] for row in annotations] == ["pass_verdict", "Observation"]
    assert all(row["score"] is None and row["label"] == "UNASSESSABLE" and row["experiment_run_id"] == "run"
               for row in annotations)


def test_case_to_example_splits_three_parts():
    case = {
        "case_id": "GEN-X-001", "question": "LX-21 功率？", "turns": [],
        "test_identity": {"logical_id": "SYN_A_STAFF001"},
        "expected_documents": ["SYN_CORPUS_LX21"], "expected_facts": ["140W"],
        "expected_behavior": "明确 140W", "pass_criteria": "含功率",
        "forbidden_disclosure": ["不得含糊"], "category": "product",
        "difficulty": "easy", "answerability": "answerable", "qtype": "fact",
        "risk": "low", "source": "synthetic", "evidence_location": "规格字段",
        "template": "T-P1", "dataset": "generated/v2",
    }
    ex = case_to_example(case)
    assert sorted(ex) == ["input", "metadata", "output"]
    assert ex["input"]["question"] == "LX-21 功率？"
    assert ex["output"]["expected_documents"] == ["SYN_CORPUS_LX21"]
    assert ex["output"]["expected_facts"] == ["140W"]
    assert ex["metadata"]["case_id"] == "GEN-X-001"
    assert ex["metadata"]["category"] == "product"


def test_collect_results_merges_behavior_and_retrieval():
    report = {
        "behavior": {"detail": [{
            "id": "C1", "pass": True, "why": "有据",
            "verdict": {"pass": True, "faithful": True, "relevant": True, "abstain": False},
            "claims": 4, "supported": 3, "answer": "LX-21 为 140W",
            "citations": [{"title": "LX-21", "chunk_id": "ch1", "document_id": "d1", "quote": "x"}],
        }]},
        "retrieval": {"per_case": [{
            "case_id": "C1",
            "metrics": {"hit@8": 1.0, "recall@8": 0.5, "mrr": 1.0},
        }]},
    }
    res = collect_results(report)
    assert list(res) == ["C1"]
    ev = {e["name"]: e for e in res["C1"]["evaluations"]}
    assert ev["answer_pass"]["score"] == 1.0
    assert ev["answer_pass"]["label"] == "PASS"
    assert ev["faithfulness_decl"]["score"] == 0.75
    assert "3/4" in ev["faithfulness_decl"]["explanation"]
    assert ev["retrieval.hit@8"]["score"] == 1.0
    assert ev["retrieval.hit@8"]["kind"] == "CODE"
    assert "retrieval" in res["C1"]["output"]
    assert res["C1"]["output"]["citations"][0]["quote"] == "x"


def test_collect_results_failure_labels():
    report = {"behavior": {"detail": [{
        "id": "C2", "pass": False, "why": "答非所问",
        "verdict": {"pass": False, "faithful": False, "relevant": False, "abstain": True},
        "claims": 0, "supported": 0, "answer": "不知道",
    }]}}
    ev = {e["name"]: e for e in collect_results(report)["C2"]["evaluations"]}
    assert ev["answer_pass"]["label"] == "FAIL"
    assert ev["answer_relevant"]["label"] == "NO"
    assert ev["abstain"]["label"] == "ABSTAIN"
    assert "faithfulness_decl" not in ev


def test_collect_results_skips_entries_without_id():
    report = {"behavior": {"detail": [{"pass": True}, {"id": "", "pass": True}]}}
    assert collect_results(report) == {}


def test_collect_results_ignores_adversarial_layer():
    """对抗层不进同步：它的 id 与评估集 case_id 零交集（实测 18/18）。

    拍平进来只会全部落进「未匹配」，看着同步了其实一条都没写。这条测试是护栏：
    若有人把 adversarial 分支加回去，results 里会凭空出现 "A1" 导致失败——
    要让它可见，得单独建一个对抗数据集，而不是在这里拼。
    """
    report = {"adversarial": {"detail": [{"id": "A1", "pass": True, "note": "正确拒绝"}]}}
    assert collect_results(report) == {}


def test_collect_results_on_empty_report():
    assert collect_results({}) == {}


def test_apply_behavior_and_retrieval_share_one_slot():
    """实时跟随按条喂、事后同步按报告喂，两条路必须落到同一个结构上。

    这是 --follow 与 --report 产出可比的前提：若两条路各写一套字段，
    界面上同一个用例在"跑的过程中"和"跑完之后"长得不一样。
    """
    res: dict = {}
    apply_retrieval(res, {"case_id": "C9", "metrics": {"hit@8": 1.0, "mrr": 0.5}})
    apply_behavior(res, {"id": "C9", "pass": True, "why": "有据",
                         "verdict": {"relevant": True, "abstain": False},
                         "claims": 2, "supported": 2, "answer": "140W"})
    ev = {e["name"]: e for e in res["C9"]["evaluations"]}
    assert ev["retrieval.hit@8"]["score"] == 1.0
    assert ev["answer_pass"]["score"] == 1.0
    assert ev["faithfulness_decl"]["score"] == 1.0
    assert res["C9"]["output"]["answer"] == "140W"
    assert res["C9"]["output"]["retrieval"]["mrr"] == 0.5
    report = {"behavior": {"detail": [{"id": "C9", "pass": True, "why": "有据",
                                       "verdict": {"relevant": True, "abstain": False},
                                       "claims": 2, "supported": 2, "answer": "140W"}]},
              "retrieval": {"per_case": [{"case_id": "C9",
                                          "metrics": {"hit@8": 1.0, "mrr": 0.5}}]}}
    want = collect_results(report)["C9"]
    assert res["C9"]["output"] == want["output"]
    assert ({e["name"]: (e["score"], e["label"]) for e in res["C9"]["evaluations"]}
            == {e["name"]: (e["score"], e["label"]) for e in want["evaluations"]})


def test_apply_helpers_ignore_records_without_id():
    res: dict = {}
    apply_behavior(res, {"pass": True})
    apply_retrieval(res, {"metrics": {"hit@8": 1.0}})
    assert res == {}


def test_experiment_name_matches_report_and_live_header():
    """实时跟随用 live 头部时间、事后同步用报告 at，两者必须算出同一个名字。

    对不上就会写两份：一份跑的过程中逐例推，一份跑完再整份推。
    """
    assert experiment_name("2026-10-21 18:30:05", "abcdef1234") == "eval-20261021-183005-abcdef1"
    assert experiment_name("2026-10-21 18:30:05", "") == "eval-20261021-183005-nogit"


def test_log_case_treats_409_as_already_logged():
    """断线重连时服务端对重复 run 回 409，必须当成"已推过"而不是中断跟随。

    这是真踩到的：跟随进程重启后按 case_id 去重（该比 dataset_example_id），
    去重失败 → 重推 → 409 → 整个跟随进程退出，界面上不再更新。
    """
    class Conflict(Exception):
        def __init__(self):
            self.response = type("R", (), {"status_code": 409})()

    class Experiments:
        def log_run(self, **kw):
            raise Conflict()

        def log_evaluation(self, **kw):
            raise AssertionError("409 之后不该继续写 evaluation")

    client = type("C", (), {"experiments": Experiments()})()
    now = datetime.now()
    assert _log_case(client, "e1", "node-1", {"output": {}, "evaluations": []}, now, now) == -1
