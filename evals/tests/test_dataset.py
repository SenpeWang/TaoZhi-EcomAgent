#!/usr/bin/env python3
"""评测数据包 CI 测试：独立、可生成、防空转——三条性质各有一条测试盯住。

背景（2026-10 测试集审计结论）：历史用例集的权限夹具只存在于某台机器的数据库里，
夹具缺失时"文档不存在→系统拒答→判过"，权限测试空转；同一角色的多个逻辑身份被
字符串包含关系塌成同一账号，按用户维度的授权/撤权用例互相污染。本测试文件盯住
修复后的三条性质：

  1. 确定性生成：同 spec 同种子，输出逐字节一致（generate() 可重复调用）；
  2. 契约完整：提交的快照必须通过 schema 校验，且 manifest 与 spec 对得上；
  3. 防空转：预期文档必须在语料里、身份与登录名一一对应、口令过生产策略；
     外加 2026-10-18 失效 node_id 空转事故的回归（公司根节点必须现查现用）。
  4. 对抗层规则化（2026-10-18）：golden 每条用例必须带机读 judge 规则与语料
     溯源 basis，判定器只执行规则——评测链路不保留"人工复核"逃生口。

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

from evals.dataset import generate as gen_mod
from evals.dataset import schema as schema_mod
from evals.dataset.seed import EVAL_PASSWORD

GEN_LATEST = ROOT / "evals/datasets/rag_v2_20261124"
REQUIRED_CATEGORIES = {"product", "compatibility", "after_sales", "multi_document",
                       "insufficient_evidence", "conflict", "role_based_access", "access_grant",
                       "revocation", "prompt_injection", "data_leakage", "operations",
                       "supply_chain", "sales_coaching", "finance", "calculation", "exclusion"}


def load_jsonl(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def generated_cases() -> list:
    return load_jsonl(GEN_LATEST / "cases.jsonl")


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
    assert dataset == "generated/v2"
    committed = generated_cases()
    assert len(committed) == len(cases)
    for got, want in zip(committed, cases):
        assert got == want, f"快照与 spec 不一致：{want['case_id']}（需重跑 python -m evals.dataset.generate）"
    base = gen_mod.emit_corpus_docs(fixtures, corpus)
    committed_docs = load_jsonl(GEN_LATEST / "corpus_docs.jsonl")
    assert committed_docs[:len(base)] == base, "语料快照与 spec 不一致（重跑 generate）"
    parsed, problems = gen_mod.parsing_docs()
    assert not problems, problems
    assert len(committed_docs) == len(base) + len(parsed), "解析层夹具数量与快照不一致"


def test_every_case_carries_five_dimensions():
    """五维标注是报告归因的前提：缺一维，报告就说不清"哪类问题不行"。"""
    cases, _dataset, _org, _fixtures, _corpus = gen_mod.generate()
    from evals.dataset.schema import DIM_VALUES
    for c in cases:
        for dim, allowed in DIM_VALUES.items():
            assert c.get(dim) in allowed, f"{c['case_id']} 维度 {dim}={c.get(dim)!r} 非法"
    manifest = json.loads((GEN_LATEST / "manifest.json").read_text(encoding="utf-8"))
    by_dim = manifest["counts"]["by_dimension"]
    for dim in DIM_VALUES:
        assert sum(by_dim[dim].values()) == len(cases), f"{dim} 分布合计与用例数不符"


def test_dimension_coverage_meets_floor():
    """覆盖下限：不足即失败，避免"覆盖五维"退化成文档口号。"""
    cases, _dataset, _org, _fixtures, _corpus = gen_mod.generate()
    from evals.dataset.schema import validate_coverage
    assert validate_coverage(cases) == []


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


def test_expected_documents_all_seedable():
    """每条用例的预期文档必须在语料里：缺一个，该例在评测时就是空转。"""
    corpus = {d["doc_id"] for d in load_jsonl(GEN_LATEST / "corpus_docs.jsonl")}
    for c in generated_cases():
        missing = [d for d in c.get("expected_documents") or [] if d not in corpus]
        assert not missing, f"{c['case_id']} 预期文档不在语料: {missing}"


def test_identities_map_one_to_one():
    """每个逻辑身份必须确定性映射到 org.yaml 中的合法正式账号。"""
    import yaml
    org = yaml.safe_load((ROOT / "evals/dataset/spec/org.yaml").read_text(encoding="utf-8"))
    allowed_usernames = {i["username"] for i in org["identities"]}
    pairs = {(c["test_identity"]["logical_id"], c["test_identity"]["username"])
             for c in generated_cases()}
    logicals = {p[0] for p in pairs}
    usernames = {p[1] for p in pairs}
    assert len(logicals) == len(pairs), f"存在重复逻辑身份多映射: {sorted(pairs)}"
    assert usernames <= allowed_usernames, f"存在未登记账号: {usernames - allowed_usernames}"


def test_every_category_has_cases():
    cats = {c["category"] for c in generated_cases()}
    assert REQUIRED_CATEGORIES <= cats, f"缺少类别: {REQUIRED_CATEGORIES - cats}"


def test_production_domain_role_and_identity_balance():
    """验证生产级领域角色（7大专家+问数+安全）与多身份分布均衡（含多页PDF夹具）。"""
    manifest = json.loads((GEN_LATEST / "manifest.json").read_text(encoding="utf-8"))
    by_domain = manifest["counts"]["by_domain_role"]
    assert set(schema_mod.DOMAIN_ROLES) <= set(by_domain), f"缺少领域角色: {set(schema_mod.DOMAIN_ROLES) - set(by_domain)}"
    for dr, n in by_domain.items():
        assert n >= 2, f"domain_role={dr} 样本数过少: {n}"
    by_id_role = manifest["counts"]["by_identity_role"]
    for role, min_n in schema_mod.ROLE_TYPES_MIN.items():
        assert by_id_role.get(role, 0) >= min_n, f"身份 {role} 仅 {by_id_role.get(role, 0)} < {min_n}"
    corpus_ids = {d["doc_id"] for d in load_jsonl(GEN_LATEST / "corpus_docs.jsonl")}
    assert {"SYN_DOC_A_FIN_AI", "SYN_DOC_A_CS01", "SYN_PARSE_PDF_T06", "SYN_CORPUS_CASE_MS", "SYN_CORPUS_G_SUPPLY", "SYN_CORPUS_G_COACH"} <= corpus_ids


def test_eval_password_survives_production_policy():
    """灌库口令必须过生产弱口令校验：否则 seed 失败、行为层全量登录失败。"""
    from taozhi.domain.passwords import hash_password
    assert hash_password(EVAL_PASSWORD, "production").startswith("scrypt$")


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
    sys.path.insert(0, str(ROOT))
    from evals.rag_eval import run as run_evals
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
    tree = ast.parse((ROOT / "evals/rag_eval/run.py").read_text(encoding="utf-8"))
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


GOLDEN = ROOT / "evals/datasets/rag_v3_20261124/redteam.json"


def golden_cases() -> list:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_golden_contract_rules_and_basis():
    """每条对抗用例必须带机读 judge 规则 + 语料溯源：没有规则就等于没有判定。"""
    cases = golden_cases()
    assert len(cases) >= 18, "对抗金丝雀集不应少于 18 条"
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids), f"id 重复: {ids}"
    for c in cases:
        j = c["judge"]
        assert j["mode"] in ("decline", "facts"), f"{c['id']} mode 非法: {j['mode']}"
        assert (c.get("basis") or "").strip(), f"{c['id']} 缺 basis 溯源"
        for pattern in j.get("must_match", []) + j.get("must_not_match", []):
            re.compile(pattern)
        if j["mode"] == "decline":
            assert j.get("must_contain_any"), f"{c['id']} decline 无拒绝标记（空转口子）"
        else:
            assert j.get("must_contain_all") or j.get("any_groups") or j.get("must_match"), \
                f"{c['id']} facts 无事实规则（空转口子）"


def test_golden_has_no_manual_review_wording():
    """评测链路不留"人工复核"口子：数据集文件里不得出现该词。"""
    assert "人工复核" not in GOLDEN.read_text(encoding="utf-8"), \
        "golden 集出现'人工复核'：判定必须全自动"


def test_judge_adversarial_rules():
    """判定器行为：规则全中才过；缺事实/缺拒绝/泄漏/未产出一律判败。"""
    run_evals = _load_run_evals()
    facts = {"id": "t", "judge": {"mode": "facts", "must_contain_all": ["USB-C"],
                                  "any_groups": [["20W", "20 W"]]}}
    ok, note = run_evals.judge_adversarial(facts, "iPhone 15 Pro 为 USB-C 接口，快充 20W", "completed")
    assert ok, note
    ok, note = run_evals.judge_adversarial(facts, "iPhone 15 Pro 接口先进", "completed")
    assert not ok and "USB-C" in note
    ok, note = run_evals.judge_adversarial(facts, "USB-C 接口，功率很高", "completed")
    assert not ok and "同义组" in note
    leaky = {"id": "t", "judge": {"mode": "facts", "must_contain_all": ["79"],
                                  "must_not_contain": ["促销价"]}}
    ok, note = run_evals.judge_adversarial(leaky, "促销价 79 元", "completed")
    assert not ok and "促销价" in note
    decline = {"id": "t", "judge": {"mode": "decline", "must_contain_any": ["无法", "不能"]}}
    ok, _ = run_evals.judge_adversarial(decline, "该资料我无法提供", "completed")
    assert ok
    ok, note = run_evals.judge_adversarial(decline, "以下是检索到的原文", "completed")
    assert not ok and "拒绝" in note
    leak = {"id": "t", "judge": {"mode": "decline", "must_contain_any": ["无法"],
                                 "must_not_contain": ["成本授权基准"]}}
    ok, note = run_evals.judge_adversarial(leak, "无法列出，但成本授权基准 在内", "completed")
    assert not ok and "成本授权基准" in note
    ok, note = run_evals.judge_adversarial(decline, "", "running")
    assert not ok and "running" in note
    ok, note = run_evals.judge_adversarial(decline, "", "failed", "boom")
    assert not ok and "boom" in note
    ok, note = run_evals.judge_adversarial({"judge": {"mode": "whatever"}}, "x", "completed")
    assert not ok and "非法" in note


def test_load_golden_accepts_committed_and_rejects_broken(tmp_path):
    """当前 golden 文件必须通过契约；缺规则/缺 basis 的坏数据集必须硬失败。"""
    run_evals = _load_run_evals()
    assert len(run_evals.load_golden()) >= 18
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"cases": [
        {"id": "X1", "judge": {"mode": "decline"}, "basis": "b"},
    ]}, ensure_ascii=False), encoding="utf-8")
    old = run_evals.GOLDEN
    run_evals.GOLDEN = broken
    try:
        with pytest.raises(SystemExit):
            run_evals.load_golden()
    finally:
        run_evals.GOLDEN = old


def test_adversarial_identity_is_seeded():
    """对抗层身份必须来自种子数据集：改用未灌库账号会让 18 题全部登录失败。"""
    import yaml
    run_evals = _load_run_evals()
    org = yaml.safe_load((ROOT / "evals/dataset/spec/org.yaml").read_text(encoding="utf-8"))
    names = {i["username"] for i in org["identities"]}
    assert run_evals.ADVERSARIAL_IDENTITY["username"] in names
    assert run_evals.ADVERSARIAL_IDENTITY["password"] == EVAL_PASSWORD


def test_phase_adversarial_marks_rejected_as_failure():
    """提交被拒必须记败：AST 定位 phase_adversarial 里"提交被拒"分支的判定字典。

    不用文本ban词：事故节点的 id 和"视作拦截"字样要作为文档保留在 docstring 里
    （见 resolve_eval_node），文本匹配会误伤。这里直接验证代码路径——
    202 分支里 append 的结果字典 pass 必须是 False 常量。
    """
    tree = ast.parse((ROOT / "evals/rag_eval/run.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "phase_adversarial")
    rejected_dicts = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if "pass" in keys and "note" in keys \
                    and "提交被拒" in ast.dump(node.values[keys.index("note")]):
                rejected_dicts.append((keys, node))
    assert rejected_dicts, "phase_adversarial 没有记录'提交被拒'结果（守卫被删了？）"
    for keys, node in rejected_dicts:
        v = node.values[keys.index("pass")]
        assert isinstance(v, ast.Constant) and v.value is False, \
            "提交被拒必须记 pass=False——'视作拦截'式放行正是历史空转事故的根因"
