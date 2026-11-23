#!/usr/bin/env python3
"""评测数据集生成器：spec/ → cases.jsonl + corpus_docs.jsonl + manifest.json。

确定性是硬要求：同一份 spec + 同一个种子，输出必须逐字节一致——
否则"基线对比"比的不是系统变化，而是生成器抖动。因此 manifest 里只有
spec 哈希与种子，没有时间戳（时间信息由 git 提交记录承担）。

用法：
  python -m evals.dataset.generate                 # 生成到 evals/datasets/generated/latest
  python -m evals.dataset.generate --out <dir>     # 指定输出目录

本生成器产出唯一评测口径：可复现、可扩展、契约校验过的数据包（手写 legacy
集已随单口径化删除，指标口径全项目唯一，见 evals/metrics.py）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "spec"
SEED = 20261018
SLOT = re.compile(r"\{([^{}]+)\}")


class A(dict):
    """属性可访问的 dict：模板槽位用 product.title 写法，eval 时才能解析。"""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _wrap(v):
    if isinstance(v, dict):
        return A({k: _wrap(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


def render(text, ctx):
    """把 {a.b.c} 槽位渲染为字符串。spec 在 git 里受review 约束，用受限 eval。"""
    if not isinstance(text, str):
        return text

    def sub(m):
        expr = m.group(1).strip()
        try:
            return str(eval(expr, {"__builtins__": {}}, ctx))
        except Exception as e:
            raise ValueError(f"槽位解析失败 {expr!r}: {e}") from e
    return SLOT.sub(sub, text)


def load_specs():
    org = yaml.safe_load((SPEC / "org.yaml").read_text(encoding="utf-8"))
    fixtures = yaml.safe_load((SPEC / "fixtures.yaml").read_text(encoding="utf-8"))
    corpus = yaml.safe_load((SPEC / "corpus.yaml").read_text(encoding="utf-8"))
    templates = yaml.safe_load((SPEC / "templates.yaml").read_text(encoding="utf-8"))
    return org, fixtures, corpus, templates


def build_context(org, fixtures, corpus):
    raw = {}
    for d in fixtures.get("documents", []):
        raw[d["id"]] = dict(d, kind="fixture")
    for p in corpus.get("products", []):
        raw[p["doc_id"]] = dict(p, kind="product", scope="company", level=1,
                                state="published", node="SYN_NODE_OP", ai_allowed=True)
    for p in corpus.get("policies", []):
        raw[p["doc_id"]] = dict(p, kind="policy", scope="company", level=1,
                                state="published", node="SYN_NODE_OP", ai_allowed=True)
    for d in corpus.get("conflict_docs", []):
        raw[d["doc_id"]] = dict(d, kind="conflict", scope="company", level=1,
                                state="published", node="SYN_NODE_OP", ai_allowed=True)
    for d in corpus.get("guides", []):
        raw[d["doc_id"]] = dict(d, kind="guide", scope="company", level=1,
                                state="published", node="SYN_NODE_OP", ai_allowed=True)
    docs = _wrap(raw)
    products = _wrap({p["key"]: p for p in corpus.get("products", [])})
    compatibilities = _wrap({
        re.sub(r"[^0-9A-Za-z]+", "_", f"{c.get('charger') or c.get('membrane')}_{c['device']}"): c
        for c in corpus.get("compatibilities", [])})
    identities = _wrap({i["logical_id"]: i for i in org.get("identities", [])})
    ctx = A(docs=docs, products=products, compatibilities=compatibilities,
            identities=identities, grants=_wrap(fixtures.get("grants", [])),
            canaries=_wrap(fixtures.get("canaries", [])))
    return ctx, docs


def product_body(p):
    f = p["facts"]
    return (f"{p['title']}\n"
            f"一、产品参数\n功率：{f['power']}\n接口：{f['ports']}\n协议：{f['protocol']}\n"
            f"重量：{f['weight']}\n价格：{f['price']} 元\n适用场景：{f['scenario']}\n"
            f"二、兼容说明\n{p['compat_note']}\n"
            f"三、注意事项\n{f['dual_caveat']}\n")


def emit_corpus_docs(fixtures, corpus):
    """灌库用文档清单：夹具 + 产品（正文由 facts 渲染）+ 政策 + 冲突对。"""
    out = []
    for d in fixtures.get("documents", []):
        out.append(dict(doc_id=d["id"], title=d["title"], body=d["body"],
                        scope=d["scope"], level=d["level"], state=d["state"],
                        node=d["node"], ai_allowed=d["ai_allowed"], kind="fixture"))
    for p in corpus.get("products", []):
        out.append(dict(doc_id=p["doc_id"], title=p["title"], body=product_body(p),
                        scope="company", level=1, state="published",
                        node="SYN_NODE_OP", ai_allowed=True, kind="product"))
    for p in corpus.get("policies", []):
        out.append(dict(doc_id=p["doc_id"], title=p["title"], body=p["body"],
                        scope="company", level=1, state="published",
                        node="SYN_NODE_OP", ai_allowed=True, kind="policy"))
    for d in corpus.get("conflict_docs", []):
        out.append(dict(doc_id=d["doc_id"], title=d["title"], body=d["body"],
                        scope="company", level=1, state="published",
                        node="SYN_NODE_OP", ai_allowed=True, kind="conflict"))
    for d in corpus.get("guides", []):
        out.append(dict(doc_id=d["doc_id"], title=d["title"], body=d["body"],
                        scope="company", level=1, state="published",
                        node="SYN_NODE_OP", ai_allowed=True, kind="guide"))
    return out


_DEFAULT_DOMAIN_BY_CAT = {
    "product": "product",
    "compat": "compatibility",
    "aftersale": "after_sales",
    "calculation": "after_sales",
    "troubleshoot": "coach",
    "coach": "coach",
    "operations": "operations",
    "supply": "supply",
    "finance": "finance",
    "chatbi": "data",
    "role": "security",
    "grant": "security",
    "revocation": "security",
    "injection": "security",
    "leakage": "security",
}


def generate():
    org, fixtures, corpus, templates = load_specs()
    ctx, docs = build_context(org, fixtures, corpus)
    version = "v2"
    dataset = f"generated/{version}"
    cases, seq = [], 0
    for tpl in templates["templates"]:
        tid = tpl["id"]
        iterate = tpl.get("iterate")
        if iterate:
            source = {"products": list(ctx["products"].values()),
                      "compatibilities": list(ctx["compatibilities"].values())}[iterate]
        else:
            source = [None]
        for item in source:
            seq += 1
            local = A(ctx)
            if iterate == "compatibilities" and item is not None:
                key = item.get("charger") or item.get("membrane")
                local["product"] = ctx["products"][key]
                local["compat"] = item
            elif item is not None:
                local["product"] = item
            for alias, ref in (tpl.get("context") or {}).items():
                local[alias] = eval(ref, {"__builtins__": {}}, ctx)
            ident = ctx["identities"][tpl["identity"]]
            ref_ans = render(tpl.get("reference_answer") or tpl["expected_behavior"], local)
            case = {
                "case_id": f"GEN-{tid}-{seq:03d}",
                "template": tid,
                "category": tpl["category"],
                "dataset": dataset,
                "test_identity": {
                    "logical_id": ident["logical_id"],
                    "username": ident["username"],
                    "note": "测试身份由 spec/org.yaml 建档，seed.py 灌库；登录名与角色一一对应",
                },
                "question": render(tpl["question"], local),
                "turns": [render(t, local) for t in tpl.get("turns", [])],
                "expected_documents": [render(d, local) for d in tpl.get("expected_documents", [])],
                "expected_facts": [render(f, local) for f in tpl.get("expected_facts", [])],
                "expected_behavior": render(tpl["expected_behavior"], local),
                "reference_answer": ref_ans,
                "forbidden_disclosure": [render(w, local) for w in tpl.get("forbidden", [])],
                "pass_criteria": tpl.get("pass_criteria", ""),
                "evidence_location": render(tpl.get("evidence", ""), local),
                "answerability": tpl["answerability"],
                "qtype": tpl["qtype"],
                "difficulty": tpl["difficulty"],
                "source": tpl["source"],
                "risk": tpl["risk"],
                "domain_role": tpl.get("domain_role") or _DEFAULT_DOMAIN_BY_CAT.get(tpl["category"], "product"),
            }
            if tpl.get("out_of_corpus"):
                case["out_of_corpus"] = True
            if tpl.get("expected_metric"):
                case["expected_metric"] = tpl["expected_metric"]
            if "input_level" in tpl:
                case["input_level"] = int(tpl["input_level"])
            if tpl.get("payload"):
                case["payload"] = dict(tpl["payload"])
            cases.append(case)
    return cases, dataset, org, fixtures, corpus


def parsing_docs():
    """解析层夹具：渲染 PDF/DOCX → 用入库解析器读回 → 正文带真实文件名与 mime。

    解析失败必须硬失败（返回非零退出码）：带着空正文或丢事实的文档灌进库，
    表现出来是"检索命中不到"，会被误判成检索问题而不是解析问题。
    """
    from .parsing import build_all

    spec_path = SPEC / "parsing.yaml"
    if not spec_path.exists():
        return [], []
    entries = yaml.safe_load(spec_path.read_text(encoding="utf-8")).get("documents", [])
    built, problems = build_all(entries)
    out = []
    for e in entries:
        _, text = built[e["doc_id"]]
        out.append(dict(doc_id=e["doc_id"], title=e["title"], body=text, scope="company",
                        level=1, state="published", node="SYN_NODE_OP", ai_allowed=True,
                        kind="parsing",
                        mime_type=("application/pdf" if e["format"] == "pdf" else
                                   "application/vnd.openxmlformats-officedocument."
                                   "wordprocessingml.document")))
    return out, problems


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="从 spec 生成独立评测数据集")
    ap.add_argument("--out", default=str(ROOT / "evals/datasets/v1_20261123/rag"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cases, dataset, org, fixtures, corpus = generate()
    corpus_docs = emit_corpus_docs(fixtures, corpus)
    parsed, problems = parsing_docs()
    if problems:
        print("解析层夹具不合格（正文为空或丢事实）：", file=sys.stderr)
        for x in problems:
            print("  -", x, file=sys.stderr)
        return 2
    corpus_docs += parsed
    by_cat = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    by_dim = {}
    for dim in ("answerability", "qtype", "difficulty", "source", "risk"):
        agg = {}
        for c in cases:
            agg[c[dim]] = agg.get(c[dim], 0) + 1
        by_dim[dim] = dict(sorted(agg.items()))
    by_domain = {}
    for c in cases:
        dr = c.get("domain_role") or "product"
        by_domain[dr] = by_domain.get(dr, 0) + 1
    role_map = {i["logical_id"]: str(i.get("role") or "employee") for i in org.get("identities", [])}
    by_id_role = {}
    for c in cases:
        rl = role_map.get((c.get("test_identity") or {}).get("logical_id"), "employee")
        by_id_role[rl] = by_id_role.get(rl, 0) + 1
    manifest = {
        "dataset": dataset,
        "seed": SEED,
        "spec_sha256": {f.name: sha256_file(f) for f in sorted(SPEC.glob("*.yaml"))},
        "counts": {"cases": len(cases), "by_category": dict(sorted(by_cat.items())),
                   "by_dimension": by_dim,
                   "by_domain_role": dict(sorted(by_domain.items())),
                   "by_identity_role": dict(sorted(by_id_role.items())),
                   "templates": len(yaml.safe_load((SPEC / "templates.yaml").read_text(encoding="utf-8"))["templates"])},
        "docs": {"total": len(corpus_docs),
                 "by_kind": {k: sum(1 for d in corpus_docs if d["kind"] == k)
                             for k in sorted({d["kind"] for d in corpus_docs})}},
        "identities": [i["logical_id"] for i in org["identities"]],
        "canaries": fixtures["canaries"],
        "notes": "确定性生成：同 spec 同种子输出逐字节一致；manifest 不含时间戳，"
                 "版本信息由 git 承担。对抗层沿用 evals/datasets/golden_adversarial.json，"
                 "其权限用例由本包夹具背书。",
    }
    (out / "cases.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    (out / "corpus_docs.jsonl").write_text(
        "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in corpus_docs), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


    print(f"生成 {len(cases)} 条用例 / {len(corpus_docs)} 篇语料文档 → {out}")
    print("类别分布:", dict(sorted(by_cat.items())))
    print("维度分布:", by_dim)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
