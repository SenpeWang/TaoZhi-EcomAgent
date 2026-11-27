"""对合成调研记录开展人工盲审和可选模型评审，按相同材料配对比较。"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import tempfile

from evals.prompts import AGENT_REVIEW_RUBRIC, EVAL_PROMPTS
from .cases import AGENT_BENCHMARK_CASES
from .contracts import validate_cases
from .harness import replay_transcript
from .reporting import fingerprint
from .runner import REPORTS_DIR, read_synthetic_replay


SAMPLE_HASHES = ("case_sha256", "observation_sha256", "material_sha256")


def blind_sheet(path: Path, cases: list[dict]) -> dict:
    """仅导出可评审维度，保留相同材料对应的摘要，人工标签初始留空。"""
    validate_cases(cases)
    catalog = {case["id"]: case for case in cases}
    captured, _ = read_synthetic_replay(path, set(catalog))
    rows = []
    for case_id, transcript in sorted(captured.items()):
        case = catalog[case_id]
        run = replay_transcript(case, transcript)
        facts = case.get("reference_facts", [])
        sections = case.get("required_sections", [])
        if not (facts or sections):
            continue
        observed = [
            {key: call.get(key) for key in ("tool_name", "status", "data_count")}
            for call in run.tool_calls if call.get("dispatched") is True
        ]
        dims = ["relevance"]
        if any(call["status"] in {"empty", "error", "timeout", "cancelled"} for call in observed):
            dims.append("gap_disclosure")
        material = {
            "task": case["prompt"], "reference_facts": facts, "required_sections": sections,
            "answer": run.report, "evidence_ids": run.evidence_ids, "observed_tools": observed,
            "dimensions": dims,
        }
        rows.append({
            "case_id": case_id, "case_sha256": fingerprint(case), "material_sha256": fingerprint(material),
            "observation_sha256": fingerprint(transcript),
            "material": material, "labels": dict.fromkeys(dims), "notes": "",
        })
    if not rows:
        raise ValueError("没有包含参考事实或交付要求的合成记录可供评审")
    instructions, _ = EVAL_PROMPTS.render("eval.agent.review", {})
    return {
        "schema_version": 1, "kind": "human", "source": "synthetic",
        "rubric": AGENT_REVIEW_RUBRIC, "instructions": instructions,
        "rubric_sha256": fingerprint([AGENT_REVIEW_RUBRIC, instructions]), "rows": rows,
    }


def _labels(value: object, dims: set[str]) -> dict:
    """校验人工标注标签与用例适用维度一致。

    Args:
        value: 待校验的标签映射。
        dims: 该用例适用的维度集合。

    Returns:
        校验通过的标签映射。
    """
    if not isinstance(value, dict) or set(value) != dims:
        raise ValueError("标签必须与该用例的适用维度一致")
    if any(label is not None and type(label) is not bool for label in value.values()):
        raise ValueError("标签仅接受 true、false、null")
    return value


def model_sheet(sheet: dict, client) -> dict:
    """通过显式选择的客户端独立评审相同材料，失败项保留原因和空标签。"""
    rows = []
    for row in sheet["rows"]:
        material = row["material"]
        dims = material["dimensions"]
        verdict = dict.fromkeys(dims)
        reason = ""
        error = None
        payload = {
            "rubric": {key: AGENT_REVIEW_RUBRIC[key] for key in dims}, "material": material,
            "output": {"labels": dict.fromkeys(dims), "reason": "逐项给出判断依据"},
        }
        try:
            response = client.chat_json(
                json.dumps(payload, ensure_ascii=False), system=EVAL_PROMPTS.render("eval.agent.review", {})[0] or "",
                schema_hint="按 output 的键返回 JSON 对象，labels 值使用布尔值或 null。",
            )
            if not isinstance(response, dict) or not isinstance(response.get("reason"), str) or not response["reason"].strip():
                raise ValueError("模型评审缺少判断理由")
            verdict = _labels(response.get("labels"), set(dims))
            reason = response["reason"]
        except Exception as exc:
            error = type(exc).__name__
        rows.append({
            "case_id": row["case_id"], **{key: row[key] for key in SAMPLE_HASHES},
            "labels": verdict, "reason": reason, "error": error,
        })
    return {
        "schema_version": 1, "kind": "model", "source": "synthetic",
        "rubric_sha256": sheet["rubric_sha256"], "rows": rows,
    }


def _review_rows(sheet: dict, kind: str) -> dict:
    """读取评审表并按用例编号建立索引。

    Args:
        sheet: 评审表对象。
        kind: 评审表类型。

    Returns:
        case_id 到评审行的映射。
    """
    if not isinstance(sheet, dict) or sheet.get("kind") != kind or sheet.get("source") != "synthetic":
        raise ValueError("评审表类型或来源不匹配")
    if type(sheet.get("schema_version")) is not int or sheet["schema_version"] != 1:
        raise ValueError("未知评审表版本")
    rows = sheet.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("评审表缺少样本")
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ValueError("评审表缺少有效用例 ID")
        if row["case_id"] in by_id:
            raise ValueError("评审表存在重复用例")
        if any(not isinstance(row.get(key), str) or len(row[key]) != 64 for key in SAMPLE_HASHES):
            raise ValueError("评审表缺少版本摘要")
        by_id[row["case_id"]] = row
    return by_id


def agreement(human: dict, model: dict) -> dict:
    """按 ID、用例、材料和量表版本配对，只统计双方都有明确判断的维度。"""
    annotators = _review_rows(human, "human")
    judges = _review_rows(model, "model")
    instructions, _ = EVAL_PROMPTS.render("eval.agent.review", {})
    rubric_sha = fingerprint([AGENT_REVIEW_RUBRIC, instructions])
    if ([human.get("rubric"), human.get("instructions")] != [AGENT_REVIEW_RUBRIC, instructions]
            or human.get("rubric_sha256") != rubric_sha or model.get("rubric_sha256") != rubric_sha):
        raise ValueError("人工与模型评审量表版本不一致")
    if annotators.keys() != judges.keys():
        raise ValueError("两份评审表的用例集合不一致，未标注的用例应保留空标签")
    counts = {key: {"applicable": 0, "paired": 0, "agree": 0} for key in AGENT_REVIEW_RUBRIC}
    disagreements = []
    for case_id, h in annotators.items():
        m = judges[case_id]
        material = h.get("material")
        if not isinstance(material, dict) or fingerprint(material) != h["material_sha256"]:
            raise ValueError(f"{case_id} 的盲审材料已改变")
        if any(h[key] != m[key] for key in SAMPLE_HASHES):
            raise ValueError(f"{case_id} 的用例或观测版本不一致")
        keys = material.get("dimensions")
        if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or key not in AGENT_REVIEW_RUBRIC for key in keys):
            raise ValueError("评审表包含未知维度")
        dims = set(keys)
        left, right = _labels(h.get("labels"), dims), _labels(m.get("labels"), dims)
        for dim in sorted(dims):
            counts[dim]["applicable"] += 1
            if left[dim] is None or right[dim] is None:
                continue
            counts[dim]["paired"] += 1
            counts[dim]["agree"] += int(left[dim] == right[dim])
            if left[dim] != right[dim]:
                disagreements.append({"case_id": case_id, "dimension": dim})
    for values in counts.values():
        values["rate"] = values["agree"] / values["paired"] if values["paired"] else None
    return {"cases": len(annotators), "dimensions": counts, "disagreements": disagreements}


def main(argv: list[str] | None = None) -> int:
    """未显式选择模型评审时，读写合成记录与人工表格全程离线。"""
    parser = argparse.ArgumentParser(description="合成调研记录的独立人工与模型评审")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--replay", type=Path, help="生成私有盲审表的合成回放 JSON")
    action.add_argument("--agreement", nargs=2, metavar=("HUMAN", "MODEL"), type=Path, help="比较两份评审表")
    parser.add_argument("--model-review", action="store_true", help="使用已配置的模型另存一份独立评审")
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR / "reviews")
    args = parser.parse_args(argv)
    try:
        if args.agreement:
            if args.model_review:
                parser.error("--model-review 仅适用于 --replay")
            sheets = [json.loads(path.read_text(encoding="utf-8")) for path in args.agreement]
            print(json.dumps(agreement(*sheets), ensure_ascii=False, indent=2))
            return 0
        sheet = blind_sheet(args.replay, AGENT_BENCHMARK_CASES)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="review-", dir=args.output_dir))
        human_path = directory / "human.json"
        human_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"人工盲审表 {human_path}")
        if args.model_review:
            from taozhi.ai.client import LLMClient

            models = set()

            def record_model(event: dict) -> None:
                """记录模型评审中实际成功调用的模型名。

                Args:
                    event: 模型调用事件。
                """
                if event.get("outcome") == "ok" and isinstance(event.get("model"), str):
                    models.add(event["model"])

            with closing(LLMClient(on_attempt=record_model)) as client:
                judged = model_sheet(sheet, client)
            judged["observed_models"] = sorted(models)
            model_path = directory / "model.json"
            model_path.write_text(json.dumps(judged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"模型评审表 {model_path}")
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
