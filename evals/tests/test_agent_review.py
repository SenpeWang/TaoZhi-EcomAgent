"""合成评审材料、人工盲标边界与逐维配对口径。"""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from evals.agent_eval import review


CASE = {
    "id": "SYN-REVIEW", "name": "合成报价", "prompt": "报价和采购限制是什么？", "required_sections": ["报价"],
    "reference_facts": [{"id": "price", "any_of": ["28 元"], "evidence_ids": ["S1"]}],
}
OBS = {
    "case_id": CASE["id"], "system_status": "COMPLETED", "report": "该渠道暂无报价。",
    "tool_calls": [{"tool_name": "search_web", "status": "empty", "data_count": 0, "dispatched": True}],
}


def write_replay(tmp_path, source="synthetic", observations=None):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({
        "source": source, "schema_version": 1, "observations": [OBS] if observations is None else observations,
    }), encoding="utf-8")
    return path


def test_blind_review_and_model_are_independent(tmp_path):
    sheet = review.blind_sheet(write_replay(tmp_path), [CASE])
    row = sheet["rows"][0]
    assert "reason" not in row and "judge" not in row
    assert row["labels"] == {"relevance": None, "gap_disclosure": None}
    row["notes"] = "保密仲裁意见"
    row["labels"]["relevance"] = False
    client = Mock()
    client.chat_json.return_value = {"labels": {"relevance": True, "gap_disclosure": True}, "reason": "说明有报价缺口"}
    model = review.model_sheet(sheet, client)
    payload = json.loads(client.chat_json.call_args.args[0])
    assert "保密仲裁意见" not in json.dumps(payload, ensure_ascii=False)
    assert "labels" not in payload["material"]
    assert model["rows"][0]["error"] is None
    assert "material" not in model["rows"][0]
    metrics = review.agreement(sheet, model)
    assert metrics["dimensions"]["relevance"] == {"applicable": 1, "paired": 1, "agree": 0, "rate": 0}
    assert metrics["dimensions"]["gap_disclosure"]["rate"] is None
    assert metrics["disagreements"] == [{"case_id": CASE["id"], "dimension": "relevance"}]
    row["labels"]["gap_disclosure"] = True
    assert review.agreement(sheet, model)["dimensions"]["gap_disclosure"]["rate"] == 1


def test_missing_or_invalid_model_review_keeps_labels_unknown(tmp_path):
    sheet = review.blind_sheet(write_replay(tmp_path), [CASE])
    client = Mock()
    client.chat_json.return_value = {"labels": {"relevance": "yes"}, "reason": "无效结果"}
    model = review.model_sheet(sheet, client)
    assert model["rows"][0]["error"] == "ValueError"
    assert all(value is None for value in model["rows"][0]["labels"].values())
    assert review.agreement(sheet, model)["dimensions"]["relevance"]["paired"] == 0


def test_version_or_material_drift_fails_pairing(tmp_path):
    sheet = review.blind_sheet(write_replay(tmp_path), [CASE])
    client = Mock()
    client.chat_json.return_value = {}
    model = review.model_sheet(sheet, client)
    for key in review.SAMPLE_HASHES:
        changed = deepcopy(model)
        changed["rows"][0][key] = "a" * 64
        with pytest.raises(ValueError, match="版本不一致"):
            review.agreement(sheet, changed)
    sheet["rows"][0]["material"]["answer"] += "修改后的回答"
    with pytest.raises(ValueError, match="材料已改变"):
        review.agreement(sheet, model)


def test_only_review_applicable_dimensions_and_reject_non_synthetic(tmp_path):
    with pytest.raises(ValueError):
        review.blind_sheet(write_replay(tmp_path, source="live"), [CASE])
    sheet = review.blind_sheet(write_replay(tmp_path, observations=[{**OBS, "tool_calls": []}]), [CASE])
    assert list(sheet["rows"][0]["labels"]) == ["relevance"]
    with pytest.raises(ValueError, match="参考事实或交付要求"):
        review.blind_sheet(write_replay(tmp_path), [{"id": CASE["id"], "name": "无参考", "prompt": "无参考"}])


def test_default_cli_generates_only_private_human_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "AGENT_BENCHMARK_CASES", [CASE])
    assert review.main(["--replay", str(write_replay(tmp_path)), "--output-dir", str(tmp_path)]) == 0
    human = next(tmp_path.glob("review-*/human.json"))
    assert human.parent.stat().st_mode & 0o077 == 0
    assert not human.with_name("model.json").exists()
