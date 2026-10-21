#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phoenix 同步的单元测试：报告 → 逐例结果的拍平逻辑。

为什么值得测：这段逻辑决定 Phoenix 界面上"一个用例看到什么分数"。拍错一次
（比如 claims=0 时算出除零、或把失败写成通过），归因就会指向错误的方向，
而且没有任何报错提醒——所以用固定输入把口径钉住。

不需要 Phoenix 在线：模块级的 import 只有标准库，phoenix 客户端在 main() 里才导入。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from sync_phoenix import case_to_example, collect_results  # noqa: E402


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
    # 金标准必须在 output：否则界面上没法判断答案错在哪
    assert ex["output"]["expected_documents"] == ["SYN_CORPUS_LX21"]
    assert ex["output"]["expected_facts"] == ["140W"]
    # case_id 必须进 metadata：同步与去重全靠它
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
    # 声明级忠实度是比率，不是布尔——这是与 answer_pass 的关键区别
    assert ev["faithfulness_decl"]["score"] == 0.75
    assert "3/4" in ev["faithfulness_decl"]["explanation"]
    # 检索指标按 retrieval.<metric> 命名，与行为层分数并列不冲突
    assert ev["retrieval.hit@8"]["score"] == 1.0
    assert ev["retrieval.hit@8"]["kind"] == "CODE"
    assert "retrieval" in res["C1"]["output"]
    # 引用正文必须进 output：只剩标题就没法在界面上判断"这句话有没有依据"
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
    # claims=0 时不能算出 0/0，必须直接不出这个维度
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
