#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索质量量化评测：在评测集抽样上对比 HyDE 开/关的命中率与延迟。

只做检索层判定（不调 LLM 生成最终答案，除 HyDE 假设段外）：
- 命中定义：检索返回的文档标题 ∈ 用例 source_files 推导的预期标题集合。
- 指标：Hit@1 / Hit@3 / Hit@8、平均与 P50/P95 延迟。
"""
import json
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals/datasets/cases.jsonl"
INCOMING = ROOT / "data/incoming/待映射"
REPORT = ROOT / "evals/reports/hyde_eval_report.json"
SAMPLE = 60

import sys
sys.path.insert(0, str(ROOT / "src"))
from ecom_copilot.enterprise.config import load_config
from ecom_copilot.enterprise.db import database_connection, fetch_one
from ecom_copilot.enterprise.policy import load_principal
from ecom_copilot.enterprise.retriever import search_documents

assert load_config().mode == "demo", "评测必须在演示库执行"


def title_of(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
    return path.stem


def main():
    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [c for c in cases if c["category"] in ("product", "compat", "aftersale", "multi_doc")]
    rng = random.Random(20261008)
    sample = rng.sample(pool, min(SAMPLE, len(pool)))

    with database_connection() as c:
        titles_in_db = {d["title"]: d["id"] for d in
                        c.execute("SELECT id,title FROM documents WHERE state='published'").fetchall()}
        principal = load_principal(c, fetch_one(
            c, "SELECT id FROM users WHERE username=%s", ("staff",))["id"])

    def expected_titles(case):
        out = set()
        for sf in case.get("source_files") or []:
            f = sf.get("file") or ""
            p = ROOT / "data" / f
            if not p.exists() and not f.startswith(("incoming/", "corpus/")):
                p = INCOMING / f
            if not p.exists():
                p = ROOT / f
            if not p.exists():
                continue
            if p.suffix == ".md":
                out.add(title_of(p))
            elif p.name in ("products.jsonl", "compatibility.jsonl", "source_index.jsonl"):
                # 结构化文件 → 同目录全部产品专题文档
                for md in p.parent.glob("*.md"):
                    out.add(title_of(md))
        return out

    def _hyde_vec(question):
        """与 pipeline._hyde_vector 同口径：脱敏 + FAST 模型 + 资料体编码。"""
        try:
            from ecom_copilot.llm.client import LLMClient, ModelTier
            from ecom_copilot.enterprise.privacy import redact
            from ecom_copilot.enterprise.embedding import encode_passages
            text = LLMClient().chat(redact(question), tier=ModelTier.FAST,
                                    system="根据问题写一段可能出现在商品资料库中的陈述句正文段落，含型号与参数细节，允许与事实不符，只输出段落。", max_tokens=220)
            if not text or len(text.strip()) < 20:
                return None
            return encode_passages([text.strip()[:800]])[0]
        except Exception:
            return None

    def run(use_hyde):
        hits = {1: 0, 3: 0, 8: 0}
        lat = []
        skipped = 0
        for case in sample:
            exp = expected_titles(case)
            if not exp:
                skipped += 1
                continue
            t0 = time.perf_counter()
            hyde_vector = _hyde_vec(case["question"]) if use_hyde else None
            with database_connection() as c:
                rows = search_documents(c, principal, case["question"], limit=8, hyde_vector=hyde_vector)
            lat.append(time.perf_counter() - t0)
            got = [r["title"] for r in rows]
            for k in hits:
                if any(t in exp for t in got[:k]):
                    hits[k] += 1
        n = len(sample) - skipped
        srt = sorted(lat)
        return {"n": n, "skipped_no_expected": skipped,
                "hit@1": round(hits[1] / n, 3), "hit@3": round(hits[3] / n, 3), "hit@8": round(hits[8] / n, 3),
                "latency_avg_s": round(sum(lat) / len(lat), 2),
                "latency_p50_s": round(srt[len(srt) // 2], 2),
                "latency_p95_s": round(srt[int(len(srt) * 0.95)], 2)}

    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "sample": SAMPLE,
              "lexical_plus_semantic": run(False), "plus_hyde": run(True)}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
