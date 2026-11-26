"""Prompt 目录契约：ID、版本、owner、渲染与冻结摘要。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evals.prompts import EVAL_PROMPTS
from evals.runtime_snapshot import algorithm_snapshot
from taozhi.ai.prompting import PromptCatalog, PromptSpec, PromptVariableError
from taozhi.application.rag.prompts import RAG_PROMPTS
from taozhi.application.research.prompts import RESEARCH_PROMPTS
from taozhi.orchestration.prompts import ORCHESTRATION_PROMPTS

ROOT = Path(__file__).resolve().parents[1]


def _all_specs():
    return (
        *RESEARCH_PROMPTS.all(),
        *RAG_PROMPTS.all(),
        *ORCHESTRATION_PROMPTS.all(),
        *EVAL_PROMPTS.all(),
    )


def test_prompt_ids_are_unique_and_versioned():
    """所有 Prompt 必须唯一、owner 合法且版本非空。"""
    specs = _all_specs()
    ids = [spec.id for spec in specs]
    assert len(ids) == len(set(ids))
    assert {spec.owner for spec in specs} == {"runtime", "eval"}
    assert all(spec.version for spec in specs)
    assert all("." in spec.id for spec in specs)


def test_prompt_catalog_checksums_are_frozen():
    """文本迁移必须保持冻结摘要；改 Prompt 需显式更新测试。"""
    assert RESEARCH_PROMPTS.catalog_checksum() == "6d89725a102c407d60f90e2dc33887c20b53e9059da59717da83a61cc6c76bed"
    assert RAG_PROMPTS.catalog_checksum() == "67ab265fbaf41059f3d3921150fe88510986e81b3058c2b123c6b399da76177e"
    assert ORCHESTRATION_PROMPTS.catalog_checksum() == "5ef1c93768378728f14cab8c44d68c96b1c0104b9ce138152c23010c2600e0be"
    assert EVAL_PROMPTS.catalog_checksum() == "fdf7fc2d8bc03a46efd7ca6d7a3209b1d7b22f8679736164078479d06b78b3c6"


def test_prompt_render_rejects_missing_variables():
    """模板缺变量必须显式失败，不能静默留下占位符。"""
    catalog = PromptCatalog(
        [PromptSpec(id="test.foo", owner="runtime", version="v1", purpose="测试", user_template="你好 {{name}}")]
    )
    assert catalog.render("test.foo", {"name": "调研员"}) == (None, "你好 调研员")
    with pytest.raises(PromptVariableError):
        catalog.render("test.foo", {})


def test_eval_prompts_do_not_import_runtime_prompt_modules():
    """评测 Prompt 必须与运行期实现物理隔离。"""
    tree = ast.parse((ROOT / "evals" / "prompts.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any(module.startswith("taozhi.application") for module in imported)


def test_runtime_snapshot_records_prompt_catalogs():
    """算法快照必须带 Prompt 目录版本与摘要。"""
    snapshot = algorithm_snapshot()
    catalogs = snapshot["prompt_catalogs"]
    assert set(catalogs) == {"research", "rag", "orchestration", "eval"}
    assert all(item["version"] and item["checksum"] for item in catalogs.values())
    assert "src/taozhi/application/research/prompts.py" in snapshot["prompt_module_sha256"]
    assert "evals/prompts.py" in snapshot["prompt_module_sha256"]