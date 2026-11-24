"""评测中心数据集版本：描述符校验、用例装载、幂等登记与历史版本登记。"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from evals import runtime_snapshot

SUITES: tuple[str, ...] = ("agent_research", "rag_core", "rag_adversarial")
LEGACY_VERSION = "v0_legacy"
MAX_CASES_PAYLOAD_BYTES = 2 * 1024 * 1024
_AGENT_SUITE = "agent_research"


def current_version() -> str:
    """返回当前仓库登记的最新数据集版本（目录名）。"""
    return runtime_snapshot.current_dataset_version()


def load_descriptor(version: str | None = None) -> dict:
    """读取数据集版本描述符。"""
    return runtime_snapshot.load_descriptor(version)


def suite_definition(version: str, suite: str) -> dict:
    """读取指定版本下某个套件的描述符定义。"""
    descriptor = load_descriptor(version)
    definition = (descriptor.get("suites") or {}).get(suite)
    if not isinstance(definition, dict):
        raise ValueError(f"数据集版本 {version} 缺少套件 {suite} 的定义")
    return definition


def validate_suite(version: str, suite: str) -> dict:
    """逐文件重算 sha256 并与描述符比对，不一致抛出 ValueError。

    Args:
        version: 数据集版本（等于 `evals/datasets/<version>` 目录名）。
        suite: 套件名，取值为 `agent_research`、`rag_core`、`rag_adversarial`。

    Returns:
        该套件的描述符定义。

    Raises:
        ValueError: 文件缺失或内容摘要与描述符不一致时抛出，消息指出文件与期望/实际摘要。
    """
    definition = suite_definition(version, suite)
    try:
        runtime_snapshot.verify_suite_files(version, suite)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    return definition


def dataset_id(version: str, suite: str) -> str:
    """由套件与版本生成稳定的数据集编号。"""
    return f"{suite}-{version}"


def version_for_content_sha(suite: str, digest: str) -> str | None:
    """按内容摘要反查数据集版本，供历史报告归属校验。"""
    if not digest:
        return None
    for version in reversed(runtime_snapshot.dataset_versions()):
        try:
            definition = suite_definition(version, suite)
        except (ValueError, RuntimeError):
            continue
        if definition.get("content_sha256") == digest:
            return version
    return None


def _payload_size(cases: Iterable[dict]) -> int:
    return len(json.dumps(list(cases), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _agent_cases(version: str, definition: dict) -> list[dict]:
    """装载指定版本的 Agent 用例。

    当前版本按 `cases.py` 全量目录复算指纹；历史版本的实测用例分类已随标准命名
    演进，无法复算历史指纹，改为校验该版本合成文件可读、用例数与描述符一致。
    """
    from evals.agent_eval.cases import AGENT_BENCHMARK_CASES, AGENT_LIVE_CASES
    from evals.agent_eval.reporting import fingerprint

    count = int(definition.get("case_count") or 0)
    if version == runtime_snapshot.current_dataset_version():
        catalog = sorted(AGENT_BENCHMARK_CASES, key=lambda case: case["id"])
        expected = definition.get("content_sha256") or ""
        actual = fingerprint(catalog)
        if expected and actual != expected:
            raise ValueError(
                f"agent_research 用例目录摘要与 {version} 描述符不一致：期望 {expected} 实际 {actual}")
    else:
        path = runtime_snapshot.DATASETS / version / "agent" / "research_cases.json"
        if not path.is_file():
            raise ValueError(f"数据集文件缺失：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        synthetic = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(synthetic, list):
            raise ValueError(f"合成用例文件结构非法：{path}")
        catalog = sorted(list(AGENT_LIVE_CASES) + synthetic, key=lambda case: case["id"])
    if count and len(catalog) != count:
        raise ValueError(f"agent_research 用例数与 {version} 描述符不一致：期望 {count} 实际 {len(catalog)}")
    return catalog


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"数据集文件缺失：{path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rag_core_cases(version: str, definition: dict) -> list[dict]:
    rows = _read_jsonl(runtime_snapshot.DATASETS / version / "rag" / "cases.jsonl")
    count = int(definition.get("case_count") or 0)
    if count and len(rows) != count:
        raise ValueError(f"rag_core 用例数与 {version} 描述符不一致：期望 {count} 实际 {len(rows)}")
    return rows


def _rag_adversarial_cases(version: str, definition: dict) -> list[dict]:
    base = runtime_snapshot.DATASETS / version / "rag"
    path = base / "redteam.json"
    if not path.is_file():
        path = base / "golden_adversarial.json"
    if not path.is_file():
        raise ValueError(f"数据集文件缺失：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"对抗用例文件结构非法：{path}")
    count = int(definition.get("case_count") or 0)
    if count and len(rows) != count:
        raise ValueError(f"rag_adversarial 用例数与 {version} 描述符不一致：期望 {count} 实际 {len(rows)}")
    return rows


def _raw_cases(version: str, suite: str) -> list[dict]:
    definition = suite_definition(version, suite)
    if suite == "agent_research":
        return _agent_cases(version, definition)
    if suite == "rag_core":
        return _rag_core_cases(version, definition)
    if suite == "rag_adversarial":
        return _rag_adversarial_cases(version, definition)
    raise ValueError(f"未知的数据集套件：{suite}")


def suite_cases(version: str, suite: str) -> list[dict]:
    """按版本与套件装载完整原始用例定义；装载前逐文件校验内容摘要。

    原始用例不做裁剪：链路透明要求界面能查看与仓库文件一致的原始字段
    （prompt、工具期望、参考事实、expected_documents、payload 等）。

    Args:
        version: 数据集版本。
        suite: 套件名。

    Returns:
        与仓库数据集文件逐字段一致的原始用例列表。

    Raises:
        ValueError: 描述符缺失、文件摘要不一致或用例数与描述符不符时抛出。
    """
    validate_suite(version, suite)
    return _raw_cases(version, suite)


def case_index(version: str | None, suite: str) -> dict[str, dict]:
    """读取用例定义索引（best effort），供入库时补充 category/name。

    Args:
        version: 数据集版本；为空时使用当前版本，历史报告可传当前版本尽力映射。
        suite: 套件名。

    Returns:
        case_id 到原始用例定义的映射；文件缺失或结构非法时返回空映射。
    """
    try:
        cases = _raw_cases(version or current_version(), suite)
    except (ValueError, RuntimeError, OSError):
        return {}
    key_field = "case_id" if suite == "rag_core" else "id"
    index: dict[str, dict] = {}
    for case in cases:
        key = case.get(key_field)
        if isinstance(key, str) and key not in index:
            index[key] = case
    return index


def dataset_metadata(version: str, suite: str) -> dict:
    """组装数据集登记元数据：保留描述符套件内容与版本来源，供触发与回放解析。"""
    descriptor = load_descriptor(version)
    definition = suite_definition(version, suite)
    return {
        **definition,
        "version": version,
        "created_at": descriptor.get("created_at") or "",
        "notes": descriptor.get("notes") or "",
    }


def dataset_fields(version: str, suite: str) -> dict:
    """按描述符组装数据集登记字段（含摘要、用例数与可展示用例）。"""
    definition = validate_suite(version, suite)
    cases = _raw_cases(version, suite)
    metadata = dataset_metadata(version, suite)
    payload = cases if _payload_size(cases) < MAX_CASES_PAYLOAD_BYTES else []
    modes = definition.get("modes") or ["live"]
    eval_kind = definition.get("eval_kind") or ("agent" if suite == _AGENT_SUITE else "rag")
    return {
        "dataset_id": dataset_id(version, suite),
        "name": suite,
        "eval_kind": eval_kind,
        "version": version,
        "description": definition.get("description") or "",
        "source": modes[0],
        "suite": suite,
        "archived": False,
        "content_sha256": definition.get("content_sha256") or "",
        "case_count": int(definition.get("case_count") or len(cases)),
        "cases_payload": payload,
        "metadata": metadata,
    }


def legacy_dataset_fields(eval_kind: str, suite: str, report_git: str | None,
                          *, source: str = "live") -> dict:
    """组装历史版本数据集字段：内容不可与当前版本比较，显式归档。"""
    resolved_source = source if source in ("live", "synthetic_trace_regrade") else "live"
    return {
        "dataset_id": f"{suite}-{LEGACY_VERSION}",
        "name": suite,
        "eval_kind": eval_kind,
        "version": LEGACY_VERSION,
        "description": f"历史版本数据集（{suite}）：用例内容已与当前版本不同，仅作历史留档，不可直接比较。",
        "source": resolved_source,
        "suite": suite,
        "archived": True,
        "content_sha256": "",
        "case_count": 0,
        "cases_payload": [],
        "metadata": {
            "legacy": True,
            "comparable": False,
            "report_git": report_git or "",
            "suite": suite,
            "version": LEGACY_VERSION,
            "eval_kind": eval_kind,
        },
    }


def ensure_legacy_dataset(conn: Any, eval_kind: str, suite: str, report_git: str | None,
                          *, source: str = "live") -> dict:
    """幂等登记历史版本数据集（archived=true，content_sha256 为空）。"""
    from taozhi.infrastructure.persistence import evaluation_repo

    return evaluation_repo.upsert_dataset(conn, legacy_dataset_fields(eval_kind, suite, report_git, source=source))


def register(conn: Any, version: str, tenant_id: str, actor_id: str,
             *, refresh: bool = False) -> list[dict]:
    """幂等登记当前版本的全部套件数据集，并为首次登记或刷新写审计。

    Args:
        conn: 数据库连接（调用方事务内）。
        version: 数据集版本。
        tenant_id: 租户编号，写入审计。
        actor_id: 操作账号编号，写入审计。
        refresh: 为 True 时用仓库原始用例覆盖已登记的 cases_payload/用例数/元数据，
            用于数据集透明化改造后的存量刷新。

    Returns:
        三个套件数据集的登记行。
    """
    from taozhi.infrastructure.persistence import evaluation_repo

    registered = []
    for suite in SUITES:
        fields = dataset_fields(version, suite)
        existing = evaluation_repo.dataset_row(conn, fields["dataset_id"])
        if existing is not None and refresh:
            conn.execute(
                "UPDATE eval_datasets SET cases_payload=%s, case_count=%s, metadata=%s, "
                "content_sha256=%s, suite=%s, updated_at=now() WHERE dataset_id=%s",
                (Jsonb(fields["cases_payload"]), fields["case_count"], Jsonb(fields["metadata"]),
                 fields["content_sha256"], fields["suite"], fields["dataset_id"]),
            )
            row = evaluation_repo.dataset_row(conn, fields["dataset_id"])
        else:
            row = evaluation_repo.upsert_dataset(conn, fields)
        if existing is None or refresh:
            evaluation_repo.insert_audit_log(
                conn, tenant_id, actor_id, "register_dataset", fields["dataset_id"],
                {
                    "version": version,
                    "suite": suite,
                    "content_sha256": fields["content_sha256"],
                    "case_count": fields["case_count"],
                    "files": sorted((fields["metadata"].get("files") or {}).keys()),
                    "refresh": bool(refresh),
                },
            )
        registered.append(row)
    return registered


def sha256_file(path: Path) -> str:
    """计算文件字节摘要。"""
    return sha256(path.read_bytes()).hexdigest()