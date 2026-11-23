"""评测中心算法版本：运行时快照与不可变版本登记（绝不写入任何密钥）。"""
from __future__ import annotations

from typing import Any

from evals import runtime_snapshot

RETRIEVAL_FIELDS: tuple[str, ...] = (
    "ann_max_distance",
    "rerank_enabled",
    "rerank_candidates",
    "rerank_timeout",
    "rerank_min_score",
    "rerank_relative",
    "quote_max_chars",
    "rerank_chars",
    "max_chunks_per_doc",
    "chunk_size",
    "chunk_overlap",
    "hyde_enabled",
    "adaptive_hyde",
    "hyde_rerank_threshold",
    "hyde_max_tokens",
    "hyde_passage_chars",
    "recency_boost",
    "entity_quota",
    "tfidf_max_features",
    "semantic_cache_threshold",
    "semantic_cache_ttl",
    "semantic_cache_max_entries",
    "es_enabled",
    "es_index",
    "es_analyzer",
    "es_search_analyzer",
)

RUNTIME_FLAGS: tuple[str, ...] = (
    "minio_enabled",
    "blob_dual_write",
    "kafka_enabled",
    "es_enabled",
    "quick_login",
)

LLM_FIELDS: tuple[str, ...] = (
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout",
    "llm_max_retries",
    "llm_empty_retries",
    "llm_output_cap",
    "llm_max_concurrency",
    "llm_direct_connection",
    "specialist_timeout",
    "specialist_max_tokens",
    "specialist_concurrency",
    "eval_timeout_seconds",
    "task_timeout",
    "max_model_calls",
    "max_attempts",
)

_EVAL_KINDS = ("agent", "rag")


def _selected(source: Any, names: tuple[str, ...]) -> dict:
    return {name: getattr(source, name) for name in names if hasattr(source, name)}


def retrieval_params(config: Any | None = None) -> dict:
    """返回检索相关运行时参数（字段名取自 Config），不含任何凭据。"""
    config = config or _load_config()
    return _selected(config, RETRIEVAL_FIELDS)


def tech_stack(config: Any | None = None) -> dict:
    """返回技术栈快照：模型标识与关键运行时开关，绝不包含 API Key 或口令。"""
    config = config or _load_config()
    return {
        "models": runtime_snapshot.model_labels(),
        "runtime": _selected(config, RUNTIME_FLAGS),
        "llm": _selected(config, LLM_FIELDS),
    }


def _load_config():
    from taozhi.config import load_config

    return load_config()


def snapshot_algorithm(version: str | None = None) -> dict:
    """组装算法版本快照：代码提交、技术栈、检索参数与 Prompt 源文件摘要。

    Args:
        version: 数据集版本（用于标注本次快照对应的数据集口径，不参与摘要计算）。

    Returns:
        含 git_sha、tech_stack、retrieval_params、prompt_snapshots 与 dataset_version 的快照。
    """
    config = _load_config()
    return {
        "git_sha": runtime_snapshot.git_sha(),
        "tech_stack": tech_stack(config),
        "retrieval_params": retrieval_params(config),
        "prompt_snapshots": runtime_snapshot.prompt_module_shas(),
        "dataset_version": version or "",
    }


def _merge_report_algorithm(snapshot: dict, algorithm: dict | None) -> tuple[str, dict, dict]:
    """把报告内嵌算法块合并到运行时快照，报告内嵌值优先。"""
    tech = dict(snapshot["tech_stack"])
    prompts = dict(snapshot["prompt_snapshots"])
    git_sha = snapshot["git_sha"]
    if not isinstance(algorithm, dict):
        return git_sha, tech, prompts
    git_sha = algorithm.get("git_sha") or git_sha
    models = algorithm.get("models")
    if isinstance(models, dict) and models:
        tech["models"] = models
    for key in ("judges_sha256", "model_config_sha256"):
        if algorithm.get(key):
            tech[key] = algorithm[key]
    report_prompts = algorithm.get("prompt_module_sha256")
    if isinstance(report_prompts, dict) and report_prompts:
        prompts = report_prompts
    return git_sha, tech, prompts


def ensure_profile(conn: Any, tenant_id: str, actor_id: str, eval_kind: str, dataset_version: str,
                   explicit_profile_id: str | None = None, notes: str = "",
                   *, algorithm: dict | None = None, version_tag: str = "") -> str:
    """登记或复用算法版本，返回 profile_id。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        actor_id: 操作账号编号。
        eval_kind: 评测类型（agent/rag）。
        dataset_version: 本次评测对应的数据集版本。
        explicit_profile_id: 显式指定的算法版本编号；存在时校验后直接使用。
        notes: 版本备注。
        algorithm: 报告内嵌算法块；缺省时使用当前运行时快照。
        version_tag: 显式版本标签；为空时由仓储自动编号。

    Returns:
        算法版本编号。

    Raises:
        ValueError: 评测类型非法、显式版本不存在或类型不匹配时抛出。
    """
    from taozhi.infrastructure.persistence import evaluation_repo

    if eval_kind not in _EVAL_KINDS:
        raise ValueError(f"未知的评测类型：{eval_kind}")
    if explicit_profile_id:
        row = evaluation_repo.profile_by_id(conn, tenant_id, explicit_profile_id)
        if row is None:
            raise ValueError(f"算法版本不存在：{explicit_profile_id}")
        if row["eval_kind"] != eval_kind:
            raise ValueError("算法版本的评测类型与报告不一致")
        return row["profile_id"]
    snapshot = snapshot_algorithm(dataset_version)
    git_sha, tech, prompts = _merge_report_algorithm(snapshot, algorithm)
    payload = {
        "eval_kind": eval_kind,
        "version_tag": version_tag,
        "git_sha": git_sha,
        "dataset_version": dataset_version,
        "tech_stack": tech,
        "retrieval_params": snapshot["retrieval_params"],
        "prompt_snapshots": prompts,
        "notes": notes,
    }
    row = evaluation_repo.insert_profile(conn, tenant_id, actor_id, payload)
    return row["profile_id"]