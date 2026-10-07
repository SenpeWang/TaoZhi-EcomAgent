"""评测运行器：加载黄金问题集 → 批量评测 → 输出报告。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings, get_settings
from ..ingestion.base import FetchRequest
from ..ingestion.sources import LocalCorpusAdapter
from ..llm.json_utils import iter_jsonl
from ..retrieval import get_retrieval_engine
from .ragas_like import evaluate_sample


def load_golden(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    settings = get_settings()
    path = Path(path) if path else settings.data_dir / "golden_questions.jsonl"
    if not path.exists():
        return []
    return list(iter_jsonl(str(path)))


def ensure_corpus_indexed() -> int:
    """确保本地语料已入索引（评测前置条件）。"""
    engine = get_retrieval_engine()
    if len(engine.index) > 0:
        return len(engine.index)
    docs = LocalCorpusAdapter().safe_fetch(FetchRequest(keywords=["商品"], limit=50))
    return engine.index_documents(docs)


def run_evaluation(path: Optional[Path] = None, top_k: int = 5,
                   limit: Optional[int] = None,
                   with_llm_judge: bool = True,
                   settings: Optional[Settings] = None) -> Dict[str, Any]:
    settings = settings or get_settings()
    samples = load_golden(path)
    if limit:
        samples = samples[:limit]
    if not samples:
        return {"error": "未找到黄金问题集", "path": str(path)}

    ensure_corpus_indexed()
    engine = get_retrieval_engine()
    started = time.time()
    rows: List[Dict[str, Any]] = []
    for sample in samples:
        rows.append(evaluate_sample(engine, sample, top_k=top_k,
                                    with_llm_judge=with_llm_judge))

    def _avg(key: str) -> float:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(values) / len(values), 4) if values else 0.0

    report = {
        "samples": len(rows),
        "elapsed_s": round(time.time() - started, 2),
        "metrics": {
            "context_precision": _avg("context_precision"),
            "context_recall": _avg("context_recall"),
            "hit@5": _avg("hit@5"),
            "faithfulness": _avg("faithfulness"),
            "answer_relevancy": _avg("answer_relevancy"),
            "citation_coverage": _avg("citation_coverage"),
        },
        "targets": {
            "context_precision": 0.85, "context_recall": 0.80, "faithfulness": 0.90,
            "answer_relevancy": 0.85, "hit@5": 0.91,
        },
        "rows": rows,
    }
    report["pass"] = {
        key: report["metrics"][key] >= report["targets"][key]
        for key in report["targets"]
    }
    return report


def save_report(report: Dict[str, Any], out_dir: Optional[Path] = None) -> Path:
    settings = get_settings()
    out_dir = Path(out_dir) if out_dir else settings.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"eval-report-{int(time.time())}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return path


def report_markdown(report: Dict[str, Any]) -> str:
    lines = ["# 检索层评测报告", "",
             f"- 样本数：{report.get('samples', 0)}",
             f"- 耗时：{report.get('elapsed_s', 0)}s", ""]
    lines.append("| 指标 | 实测 | 目标 | 结论 |")
    lines.append("| --- | --- | --- | --- |")
    for key, target in report.get("targets", {}).items():
        actual = report.get("metrics", {}).get(key, 0.0)
        ok = report.get("pass", {}).get(key, False)
        lines.append(f"| {key} | {actual:.3f} | {target:.2f} | {'✅' if ok else '⚠️'} |")
    lines.append("")
    lines.append("## 逐条明细")
    lines.append("| 问题 | Precision | Recall | Hit@5 | Faithfulness | Relevancy |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in report.get("rows", []):
        lines.append(
            f"| {row['question'][:30]} | {row.get('context_precision', 0):.2f} | "
            f"{row.get('context_recall', 0):.2f} | {row.get('hit@5', 0):.0f} | "
            f"{row.get('faithfulness') or 0:.2f} | {row.get('answer_relevancy') or 0:.2f} |"
        )
    return "\n".join(lines)
