#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用例契约校验：字段完整性、类别合法、身份可解析、锚点存在、夹具引用有效。

为什么需要它：手写用例集的两个慢性病——① 字段漂移（有人加 category 忘了加
expected_facts，评测跑到一半才崩）；② 引用失效（expected_documents 指向不存在的
夹具，跑出来是"文档不存在→拒答→判过"的空转）。本模块把契约变成机器检查，
CI 里强制执行，写入数据集之前就拦住。
"""
from __future__ import annotations

import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parent / "spec"

# 检索类：必须有可判定锚点与预期文档（否则"答没答对"无法机器判断）
RETRIEVAL_CATEGORIES = ("product", "compat", "aftersale", "multi_doc", "insufficient", "conflict",
                        "robust", "troubleshoot", "edge", "timeliness", "citation", "long_context")
# 模糊/歧义类：不能有单点答案，判定靠 pass_criteria（是否澄清/分情况），不强制锚点事实
AMBIGUOUS_CATEGORIES = ("ambiguous",)
# 权限类：判定口径必须写死（pass_criteria），不允许 judge 自由发挥
PERMISSION_CATEGORIES = ("role", "grant", "revocation", "injection", "leakage")
# 多轮类：必须有前置轮，判定只针对最后一轮；预期锚点靠 expected_facts 或 pass_criteria
MULTI_TURN_CATEGORIES = ("multi_turn",)
# 解析层：必须有预期文档与可判定事实，否则测不出"读进来还在不在"
PARSING_CATEGORIES = ("parsing",)
KNOWN_CATEGORIES = (RETRIEVAL_CATEGORIES + PERMISSION_CATEGORIES
                    + MULTI_TURN_CATEGORIES + PARSING_CATEGORIES + AMBIGUOUS_CATEGORIES)

# 五维标注：任何一条用例都必须带齐，否则报告无法按维度归因
DIM_VALUES = {
    "answerability": ("answerable", "partial", "unanswerable"),
    "qtype": ("fact", "process", "multihop", "compare", "troubleshoot",
              "edge", "ambiguous", "security"),
    "difficulty": ("easy", "medium", "hard"),
    # real = 线上日志采样，当前无日志通道，仅保留取值以便接入
    "source": ("synthetic", "badcase", "adversarial", "real"),
    "risk": ("high", "medium", "low"),
}

# 覆盖下限：维度矩阵不能只写进文档，数量不够就拒绝生成——
# 否则"覆盖 5 个维度"会退化成一句无法验证的话。
COVERAGE_MIN = {
    "answerability": {"answerable": 60, "partial": 8, "unanswerable": 18},
    "qtype": {"fact": 40, "process": 3, "multihop": 4, "compare": 8,
              "troubleshoot": 5, "edge": 5, "ambiguous": 4, "security": 14},
    "difficulty": {"easy": 20, "medium": 25, "hard": 20},
    "source": {"synthetic": 60, "badcase": 4, "adversarial": 4},
    "risk": {"high": 15, "medium": 10, "low": 30},
}

REQUIRED_FIELDS = ("case_id", "category", "dataset", "test_identity", "question",
                   "expected_behavior", "pass_criteria", "forbidden_disclosure")
IDENTITY_FIELDS = ("logical_id", "username")


class SchemaError(Exception):
    pass


def load_spec():
    import yaml
    org = yaml.safe_load((SPEC / "org.yaml").read_text(encoding="utf-8"))
    fixtures = yaml.safe_load((SPEC / "fixtures.yaml").read_text(encoding="utf-8"))
    corpus = yaml.safe_load((SPEC / "corpus.yaml").read_text(encoding="utf-8"))
    templates = yaml.safe_load((SPEC / "templates.yaml").read_text(encoding="utf-8"))
    return org, fixtures, corpus, templates


def known_doc_ids() -> set:
    """spec 里全部文档 id：权限夹具 + 检索语料 + 政策 + 冲突对。"""
    _, fixtures, corpus, _ = load_spec()
    ids = {d["id"] for d in fixtures.get("documents", [])}
    ids |= {p["doc_id"] for p in corpus.get("products", [])}
    ids |= {p["doc_id"] for p in corpus.get("policies", [])}
    ids |= {d["doc_id"] for d in corpus.get("conflict_docs", [])}
    ids |= {d["doc_id"] for d in corpus.get("guides", [])}
    parsing_path = SPEC / "parsing.yaml"
    if parsing_path.exists():
        import yaml
        parsing = yaml.safe_load(parsing_path.read_text(encoding="utf-8"))
        ids |= {d["doc_id"] for d in parsing.get("documents", [])}
    return ids


def known_identities() -> set:
    org, _, _, _ = load_spec()
    return {i["logical_id"] for i in org.get("identities", [])}


def known_canaries() -> set:
    _, fixtures, _, _ = load_spec()
    return set(fixtures.get("canaries", []))


def validate_case(case: dict, doc_ids: set, identities: set, canaries: set,
                  seen_ids: set, seen_questions: set) -> list:
    """返回该用例的问题列表（空列表 = 通过）。"""
    errs = []
    cid = case.get("case_id", "<无 case_id>")
    for f in REQUIRED_FIELDS:
        if f not in case:
            errs.append(f"{cid}: 缺字段 {f}")
    if case.get("category") not in KNOWN_CATEGORIES:
        errs.append(f"{cid}: 未知类别 {case.get('category')!r}")
    for dim, allowed in DIM_VALUES.items():
        v = case.get(dim)
        if v is None:
            errs.append(f"{cid}: 缺维度 {dim}")
        elif v not in allowed:
            errs.append(f"{cid}: 维度 {dim}={v!r} 不在 {allowed}")
    if case.get("qtype") == "security" and case.get("risk") != "high":
        errs.append(f"{cid}: 安全类用例风险等级必须为 high")
    if case.get("source") == "badcase" and not (case.get("evidence_location") or "").strip():
        errs.append(f"{cid}: 历史 bad case 必须给 evidence_location（失败可追溯）")
    if case.get("category") in AMBIGUOUS_CATEGORIES and not (case.get("pass_criteria") or "").strip():
        errs.append(f"{cid}: 模糊/歧义类必须给 pass_criteria（判定澄清或分情况）")
    ident = case.get("test_identity") or {}
    for f in IDENTITY_FIELDS:
        if not ident.get(f):
            errs.append(f"{cid}: test_identity 缺 {f}")
    if ident.get("logical_id") and ident["logical_id"] not in identities:
        errs.append(f"{cid}: 身份 {ident['logical_id']} 不在 org.yaml")
    q = (case.get("question") or "").strip()
    # 多轮的追问天然是短句（"那重量呢？"），按单轮标准判会把真实问法判成脏数据
    min_len = 4 if case.get("category") in MULTI_TURN_CATEGORIES else 8
    if len(q) < min_len:
        errs.append(f"{cid}: 问题过短（{len(q)} 字符），不像真实问法")
    if q in seen_questions:
        errs.append(f"{cid}: 问题重复（与更早用例相同）")
    seen_questions.add(q)
    cat = case.get("category")
    if cat in RETRIEVAL_CATEGORIES + PARSING_CATEGORIES:
        facts = case.get("expected_facts") or []
        if not facts:
            errs.append(f"{cid}: 检索类用例必须给 expected_facts（可判定锚点）")
        docs = case.get("expected_documents") or []
        if not docs:
            errs.append(f"{cid}: 检索类用例必须给 expected_documents")
        for d in docs:
            if d not in doc_ids:
                errs.append(f"{cid}: 预期文档 {d} 不在 spec（会空转：查无此文档→拒答→判过）")
    if cat in PERMISSION_CATEGORIES and not (case.get("pass_criteria") or "").strip():
        errs.append(f"{cid}: 权限类用例必须给 pass_criteria")
    if cat in MULTI_TURN_CATEGORIES:
        turns = case.get("turns") or []
        if not turns:
            errs.append(f"{cid}: 多轮用例必须给 turns（前置轮），否则退化成单轮")
        for t in turns:
            if len((t or "").strip()) < 6:
                errs.append(f"{cid}: 前置轮过短，无法建立上下文")
        if not (case.get("expected_facts") or (case.get("pass_criteria") or "").strip()):
            errs.append(f"{cid}: 多轮用例必须给 expected_facts 或 pass_criteria")
    # 禁区里的金丝雀必须真实存在：写错的标记名等于没有红线
    for w in case.get("forbidden_disclosure") or []:
        if w.startswith("SYN-CANARY-") and w not in canaries:
            errs.append(f"{cid}: 禁区标记 {w} 不在 fixtures.canaries（拼写错误即红线失效）")
    if cid in seen_ids:
        errs.append(f"{cid}: case_id 重复")
    seen_ids.add(cid)
    return errs


def validate_coverage(cases: list) -> list:
    """维度覆盖下限：数量不够就报错，避免矩阵退化成一句口号。"""
    errs = []
    for dim, mins in COVERAGE_MIN.items():
        actual = {}
        for c in cases:
            actual[c.get(dim)] = actual.get(c.get(dim), 0) + 1
        for value, min_n in mins.items():
            got = actual.get(value, 0)
            if got < min_n:
                errs.append(f"维度 {dim}={value} 仅 {got} 例，低于下限 {min_n}")
    return errs


def validate_cases(cases: list) -> list:
    doc_ids, identities, canaries = known_doc_ids(), known_identities(), known_canaries()
    seen_ids, seen_questions = set(), set()
    errs = []
    for case in cases:
        errs += validate_case(case, doc_ids, identities, canaries, seen_ids, seen_questions)
    errs += validate_coverage(cases)
    return errs


def main() -> int:
    import json
    target = sys.argv[1] if len(sys.argv) > 1 else "evals/datasets/generated/latest/cases.jsonl"
    path = Path(target)
    if not path.exists():
        print(f"数据集不存在: {path}", file=sys.stderr)
        return 2
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    errs = validate_cases(cases)
    if errs:
        print(f"✗ {len(cases)} 条用例中 {len(errs)} 个契约问题：", file=sys.stderr)
        for e in errs[:40]:
            print("  -", e, file=sys.stderr)
        return 1
    print(f"✓ {len(cases)} 条用例全部通过契约校验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
