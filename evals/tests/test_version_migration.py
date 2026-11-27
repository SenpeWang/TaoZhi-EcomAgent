"""历史数据集版本前缀迁移的纯函数测试。"""
from evals.center import version_migration


def test_target_version_adds_kind_prefix():
    """验证目标版本号自动补上体系前缀。"""
    assert version_migration.target_version("rag", "v1_20261123") == "rag_v1_20261123"
    assert version_migration.target_version("agent", "v2_20261124") == "agent_v2_20261124"


def test_target_version_is_idempotent():
    """验证已带前缀的版本号重复处理保持不变。"""
    assert version_migration.target_version("rag", "rag_v3_20261124") == "rag_v3_20261124"
    assert version_migration.target_version("agent", "agent_v3_20261124") == "agent_v3_20261124"


def test_target_dataset_id_uses_suite_and_prefixed_version():
    """验证目标数据集标识由套件与带前缀版本拼接。"""
    assert version_migration.target_dataset_id("rag_core", "rag_v1_20261123") == \
        "rag_core-rag_v1_20261123"
    assert version_migration.target_dataset_id("agent_research", "agent_v1_20261123") == \
        "agent_research-agent_v1_20261123"


def test_rewrite_files_strips_legacy_prefix():
    """验证迁移改写文件路径时去掉旧前缀。"""
    files = {"agent/research_cases.json": "a", "agent/web_replay.json": "b"}
    assert version_migration.rewrite_files("agent", files) == {
        "research_cases.json": "a", "web_replay.json": "b"}
    rag_files = {"rag/cases.jsonl": "c", "rag/redteam.json": "d"}
    assert version_migration.rewrite_files("rag", rag_files) == {
        "cases.jsonl": "c", "redteam.json": "d"}


def test_rewrite_files_keeps_relative_names():
    """验证已是相对路径的文件名保持不变。"""
    assert version_migration.rewrite_files("agent", {"research_cases.json": "x"}) == {
        "research_cases.json": "x"}
