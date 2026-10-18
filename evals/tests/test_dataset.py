#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测数据包 CI 测试：独立、可生成、防空转——三条性质各有一条测试盯住。

背景（2026-10 测试集审计结论）：历史用例集的权限夹具只存在于某台机器的数据库里，
夹具缺失时"文档不存在→系统拒答→判过"，权限测试空转；同一角色的多个逻辑身份被
字符串包含关系塌成同一账号，按用户维度的授权/撤权用例互相污染。本测试文件盯住
修复后的三条性质：

  1. 确定性生成：同 spec 同种子，输出逐字节一致（generate() 可重复调用）；
  2. 契约完整：提交的快照必须通过 schema 校验，且 manifest 与 spec 对得上；
  3. 防空转：预期文档必须在语料里、身份与登录名一一对应、口令过生产策略；
     外加 2026-10-18 失效 node_id 空转事故的回归（公司根节点必须现查现用）。

运行：.venv/bin/python -m pytest evals/tests -q
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from evals.dataset import generate as gen_mod      # noqa: E402
from evals.dataset import schema as schema_mod     # noqa: E402
from evals.dataset.seed import EVAL_PASSWORD       # noqa: E402

GEN_LATEST = ROOT / "evals/datasets/generated/latest"
REQUIRED_CATEGORIES = {"product", "compat", "aftersale", "multi_doc", "insufficient",
                       "conflict", "role", "grant", "revocation", "injection", "leakage"}


def load_jsonl(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def generated_cases() -> list:
    return load_jsonl(GEN_LATEST / "cases.jsonl")


# ─────────────────── 性质一：确定性生成 ───────────────────
def test_generation_is_deterministic():
    """同 spec 同种子调用两次，用例与语料必须逐字段一致。"""
    a = gen_mod.generate()
    b = gen_mod.generate()
    assert json.dumps(a[0], ensure_ascii=False, sort_keys=True) == \
           json.dumps(b[0], ensure_ascii=False, sort_keys=True)
    docs_a = gen_mod.emit_corpus_docs(a[3], a[4])
    docs_b = gen_mod.emit_corpus_docs(b[3], b[4])
    assert json.dumps(docs_a, ensure_ascii=False, sort_keys=True) == \
           json.dumps(docs_b, ensure_ascii=False, sort_keys=True)


def test_committed_snapshot_matches_current_spec():
    """磁盘快照必须与当前 spec 重新生成的结果一致——改了 spec 忘了重新生成会被挡住。"""
    cases, dataset, _org, fixtures, corpus = gen_mod.generate()
    assert dataset == "generated/v1"
    committed = generated_cases()
    assert len(committed) == len(cases)
    for got, want in zip(committed, cases):
        assert got == want, f"快照与 spec 不一致：{want['case_id']}（需重跑 python -m evals.dataset.generate）"
    assert load_jsonl(GEN_LATEST / "corpus_docs.jsonl") == gen_mod.emit_corpus_docs(fixtures, corpus)


# ─────────────────── 性质二：契约完整 ───────────────────
def test_committed_cases_pass_schema():
    errs = schema_mod.validate_cases(generated_cases())
    assert not errs, "契约问题:\n" + "\n".join(errs[:20])


def test_manifest_matches_spec_and_snapshot():
    manifest = json.loads((GEN_LATEST / "manifest.json").read_text(encoding="utf-8"))
    for f in sorted((ROOT / "evals/dataset/spec").glob("*.yaml")):
        assert manifest["spec_sha256"][f.name] == gen_mod.sha256_file(f), \
            f"{f.name} 已改动但 manifest 未更新（重跑 generate）"
    cases = generated_cases()
    assert manifest["counts"]["cases"] == len(cases)
    by_cat: dict = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    assert manifest["counts"]["by_category"] == by_cat
    assert manifest["docs"]["total"] == len(load_jsonl(GEN_LATEST / "corpus_docs.jsonl"))


# ─────────────────── 性质三：防空转 ───────────────────
def test_expected_documents_all_seedable():
    """每条用例的预期文档必须在语料里：缺一个，该例在评测时就是空转。"""
    corpus = {d["doc_id"] for d in load_jsonl(GEN_LATEST / "corpus_docs.jsonl")}
    for c in generated_cases():
        missing = [d for d in c.get("expected_documents") or [] if d not in corpus]
        assert not missing, f"{c['case_id']} 预期文档不在语料: {missing}"


def test_identities_map_one_to_one():
    """逻辑身份→登录名必须一一对应：多个身份塌成同一账号，授权类用例互相污染。"""
    pairs = {(c["test_identity"]["logical_id"], c["test_identity"]["username"])
             for c in generated_cases()}
    logicals = {p[0] for p in pairs}
    usernames = {p[1] for p in pairs}
    assert len(logicals) == len(pairs) == len(usernames), f"身份与登录名不是一一对应: {sorted(pairs)}"


def test_every_category_has_cases():
    cats = {c["category"] for c in generated_cases()}
    assert REQUIRED_CATEGORIES <= cats, f"缺少类别: {REQUIRED_CATEGORIES - cats}"


def test_eval_password_survives_production_policy():
    """灌库口令必须过生产弱口令校验：否则 seed 失败、行为层全量登录失败。"""
    from ecom_copilot.enterprise.passwords import hash_password
    assert hash_password(EVAL_PASSWORD, "production").startswith("scrypt$")


# ─────────────── 回归：失效 node_id 空转事故（2026-10-18） ───────────────
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """按调用顺序返回预设行的假连接（db.fetch_one 只用到 execute().fetchone()）。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def execute(self, sql, params=()):
        return _FakeResult(self._rows.pop(0) if self._rows else None)


def _load_run_evals():
    sys.path.insert(0, str(ROOT / "evals"))
    import run_evals  # noqa: PLC0415
    return run_evals


def test_resolve_eval_node_returns_company_root():
    run_evals = _load_run_evals()
    conn = _FakeConn([{"id": "tenant_x"}, {"id": "org_root_x"}])
    assert run_evals.resolve_eval_node(conn) == "org_root_x"


def test_resolve_eval_node_hard_fails_when_company_root_missing():
    """公司根节点/租户查不到必须硬失败：历史事故里失效 node_id 让对抗层 18/18 空过。"""
    run_evals = _load_run_evals()
    with pytest.raises(SystemExit):
        run_evals.resolve_eval_node(_FakeConn([{"id": "tenant_x"}, None]))
    with pytest.raises(SystemExit):
        run_evals.resolve_eval_node(_FakeConn([None]))


def test_run_evals_has_no_hardcoded_node():
    """node_id 必须现查现用：AST 层面再出现写死的 org_ 节点即为回归。

    用 AST 而不是文本匹配：事故节点的 id 会保留在 docstring 里作为事故记录，
    文本匹配会误伤；只查"赋值给 org_ 字面量"与"node_id= 字面量"两种真实硬编码形态。
    """
    tree = ast.parse((ROOT / "evals/run_evals.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str) \
                        and re.fullmatch(r"org_[0-9a-f]{8,}", node.value.value):
                    bad.append(f"{target.id} = {node.value.value!r}")
        if isinstance(node, ast.keyword) and node.arg == "node_id" \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            bad.append(f"node_id={node.value.value!r}")
    assert not bad, f"run_evals.py 出现写死的组织节点: {bad}"
