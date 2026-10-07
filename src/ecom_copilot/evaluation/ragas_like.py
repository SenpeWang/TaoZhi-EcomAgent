"""RAGAS 风格评测指标（PRD 7.1 / 7.2）：本地可复现，LLM-as-Judge 可选。

* Context Precision —— 召回上下文中命中金标关键词的比例
* Context Recall    —— 金标关键词被召回覆盖的比例
* Faithfulness      —— 答案论断是否可由上下文支撑（LLM-as-Judge / 词面重叠兜底）
* Answer Relevancy  —— 答案与问题的相关度
* Hit@5             —— 前 5 条召回是否命中任一金标关键词
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from ..llm import ModelTier, get_llm
from ..llm.json_utils import extract_json
from ..observability import inc
from ..retrieval.engine import RetrievalEngine
from ..schemas.common import Evidence, PermissionContext


def context_precision(contexts: Sequence[str], keywords: Sequence[str]) -> float:
    if not contexts:
        return 0.0
    hit = sum(1 for c in contexts if any(k in c for k in keywords))
    return hit / len(contexts)


def context_recall(contexts: Sequence[str], keywords: Sequence[str]) -> float:
    if not keywords:
        return 1.0
    joined = "\n".join(contexts)
    covered = sum(1 for k in keywords if k in joined)
    return covered / len(keywords)


def hit_at_k(contexts: Sequence[str], keywords: Sequence[str], k: int = 5) -> float:
    top = list(contexts)[:k]
    return 1.0 if any(any(kw in c for kw in keywords) for c in top) else 0.0


_FAITHFULNESS_PROMPT = """判断答案中的每个论断是否能被给定上下文支撑。

问题：{question}
上下文：
{context}
答案：{answer}

输出 JSON：{{"supported_claims":<整数>,"total_claims":<整数>,"score":<0-1>}}
"""

_RELEVANCY_PROMPT = """判断答案与问题的相关程度（0-1），并指出是否答非所问。

问题：{question}
答案：{answer}
输出 JSON：{{"score":<0-1>,"reason":""}}
"""


def faithfulness(question: str, answer: str, contexts: Sequence[str]) -> float:
    if not answer:
        return 0.0
    if not get_llm().settings.has_llm:
        return _lexical_faithfulness(answer, contexts)
    prompt = _FAITHFULNESS_PROMPT.format(
        question=question,
        context="\n".join(f"- {c[:400]}" for c in list(contexts)[:6]),
        answer=answer[:1500],
    )
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=500)
        data = extract_json(raw, expect="object") or {}
        score = data.get("score")
        if isinstance(score, (int, float)):
            return float(score)
        supported = float(data.get("supported_claims", 0) or 0)
        total = float(data.get("total_claims", 1) or 1)
        return supported / total if total else 0.0
    except Exception:  # noqa: BLE001
        return _lexical_faithfulness(answer, contexts)


def answer_relevancy(question: str, answer: str) -> float:
    if not answer:
        return 0.0
    if not get_llm().settings.has_llm:
        return _lexical_overlap(question, answer)
    prompt = _RELEVANCY_PROMPT.format(question=question, answer=answer[:1200])
    try:
        raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=400)
        data = extract_json(raw, expect="object") or {}
        score = data.get("score")
        return float(score) if isinstance(score, (int, float)) else _lexical_overlap(question, answer)
    except Exception:  # noqa: BLE001
        return _lexical_overlap(question, answer)


def citation_coverage(answer: str) -> float:
    """引用溯源覆盖率：含引用标记的句子占比。"""
    sentences = [s for s in re.split(r"(?<=[。！？；])", answer) if s.strip()]
    if not sentences:
        return 0.0
    with_ref = sum(1 for s in sentences if "[" in s and "]" in s)
    return with_ref / len(sentences)


def _lexical_faithfulness(answer: str, contexts: Sequence[str]) -> float:
    joined = "\n".join(contexts)
    fragments = [f for f in re.split(r"(?<=[。；])", answer) if len(f.strip()) > 8]
    if not fragments:
        return 0.0
    supported = 0
    for frag in fragments:
        terms = re.findall(r"[\u4e00-\u9fa5]{2,6}", frag)[:6]
        if terms and sum(1 for t in terms if t in joined) / len(terms) >= 0.5:
            supported += 1
    return supported / len(fragments)


def _lexical_overlap(question: str, answer: str) -> float:
    terms = [t for t in re.findall(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z]{3,}", question)]
    if not terms:
        return 0.0
    hit = sum(1 for t in terms if t in answer)
    return round(hit / len(terms), 3)


def evaluate_sample(engine: RetrievalEngine, sample: Dict[str, Any], top_k: int = 5,
                    permission: Optional[PermissionContext] = None,
                    with_llm_judge: bool = True) -> Dict[str, Any]:
    """对单条金标问题做检索层评测。"""
    question = sample["question"]
    keywords = sample.get("expected_keywords", []) or []
    hits = engine.retrieve(question, top_k=top_k, permission=permission)
    contexts = [h.chunk.text for h in hits]
    answer = ""
    if with_llm_judge and get_llm().settings.has_llm:
        answer = _generate_answer(question, contexts)
    inc("eval.samples")
    return {
        "id": sample.get("id", ""),
        "question": question,
        "context_precision": round(context_precision(contexts, keywords), 3),
        "context_recall": round(context_recall(contexts, keywords), 3),
        "hit@5": hit_at_k(contexts, keywords, k=5),
        "faithfulness": round(faithfulness(question, answer, contexts), 3) if answer else None,
        "answer_relevancy": round(answer_relevancy(question, answer), 3) if answer else None,
        "citation_coverage": round(citation_coverage(answer), 3) if answer else None,
        "hits": [{"doc": h.chunk.doc_name, "channel": h.channel,
                  "score": round(h.score, 4)} for h in hits],
    }


def _generate_answer(question: str, contexts: Sequence[str]) -> str:
    context_text = "\n".join(f"[{i}] {c[:400]}" for i, c in enumerate(list(contexts)[:6]))
    prompt = (
        f"你是产业研究助手。请仅依据下列证据回答问题，每句结论后标注引用编号如 [0]。\n\n"
        f"证据：\n{context_text}\n\n问题：{question}\n\n回答："
    )
    try:
        return get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=800)
    except Exception:  # noqa: BLE001
        return ""
