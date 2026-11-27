"""评测中心唯一入库编排：报告校验 → 批次/逐例落库 → 原始报告 → 审计与基线。

Agent 报告沿用 `evals.agent_eval.publish` 的投影口径；RAG 报告按三层指标投影。
所有写入在调用方事务内完成，重复入库只返回 created=false 且不产生副作用。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from evals import runtime_snapshot
from evals.center import baselines, datasets, profiles
from taozhi.domain.evaluation_modes import REAL_EVAL_MODES
from taozhi.application.evaluation import dataset_snapshots as snapshots
from taozhi.infrastructure.persistence import evaluation_repo
from taozhi.infrastructure.persistence.database import database_connection

AGENT_DATASET = "agent_research"
AGENT_MIN_SCHEMA = 3
SCOPE_VALUES = ("full", "quick", "retrieval", "adversarial", "behavior")
MODE_VALUES = ("live", "synthetic_trace_regrade")
_RAG_LAYERS = ("retrieval", "adversarial", "behavior", "six_metrics")
_DROPPED_LAYER_KEYS = ("per_case", "detail")


def file_sha256(path: Path) -> str:
    """计算报告文件字节摘要。"""
    return sha256(Path(path).read_bytes()).hexdigest()


def canonical_json(value: Any) -> bytes:
    """把对象序列化为规范化 JSON 字节，供稳定摘要使用。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def case_sha256(value: Any) -> str:
    """对用例定义求规范化 JSON 摘要。"""
    return sha256(canonical_json(value)).hexdigest()


def resolve_owner(tenant_code: str = "mofa", owner_username: str = "owner") -> tuple[str, str]:
    """按租户编码与账号登录名解析入库归属。

    Args:
        tenant_code: 企业租户编码。
        owner_username: 批次发起人的登录名。

    Returns:
        (租户编号, 账号编号)。

    Raises:
        ValueError: 租户或账号不存在时抛出。
    """
    from taozhi.infrastructure.persistence.database import database_connection, fetch_one

    with database_connection() as conn:
        tenant = fetch_one(conn, "SELECT id FROM tenants WHERE code=%s", (tenant_code,))
        if tenant is None:
            raise ValueError(f"租户编码不存在：{tenant_code}")
        user = fetch_one(conn, "SELECT id FROM users WHERE tenant_id=%s AND username=%s",
                         (tenant["id"], owner_username))
        if user is None:
            raise ValueError(f"账号不存在：{owner_username}")
    return tenant["id"], user["id"]


def detect_kind(raw: dict, kind: str = "auto") -> str:
    """识别报告类型：agent 报告带 schema/run_id/case_results，其余按 RAG 三层报告处理。"""
    if kind in ("agent", "rag"):
        return kind
    if kind != "auto":
        raise ValueError(f"未知的报告类型：{kind}")
    if not isinstance(raw, dict):
        raise ValueError("报告内容必须是 JSON 对象")
    schema = raw.get("schema_version")
    if isinstance(schema, int) and (raw.get("run_id") or raw.get("case_results") is not None):
        return "agent"
    if raw.get("case_results") is not None:
        return "agent"
    if any(name in raw for name in ("six_metrics", "retrieval", "adversarial", "behavior")):
        return "rag"
    raise ValueError("无法识别评测报告类型（既非 Agent schema 报告也非 RAG 三层报告）")


def _number(value: Any) -> float | None:
    """把取值规整为浮点数，非数值返回 None。

    Args:
        value: 待规整的取值。

    Returns:
        浮点数或 None。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _score(value: Any) -> float | None:
    """把取值裁剪到 0~1 并保留六位小数。

    Args:
        value: 原始分数。

    Returns:
        规整后的分数或 None。
    """
    number = _number(value)
    if number is None:
        return None
    return round(min(1.0, max(0.0, number)), 6)


def _milliseconds(seconds: Any) -> int | None:
    """把秒级时延换算为毫秒整数。

    Args:
        seconds: 秒级时延。

    Returns:
        毫秒整数或 None。
    """
    number = _number(seconds)
    return None if number is None else int(round(number * 1000))


def _report_datetime(raw: dict, path: Path) -> datetime:
    """解析报告生成时间：优先 at 字段，其次文件名时间戳；统一按 UTC 落库。"""
    at = raw.get("at")
    if isinstance(at, str) and at.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d_%H%M%S"):
            try:
                return datetime.strptime(at.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    parts = Path(path).stem.split("-")
    if len(parts) >= 2:
        try:
            return datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError("报告缺少可解析的生成时间（at 字段或文件名时间戳）")


def infer_scope(raw: dict) -> str:
    """按实际执行的层推断 RAG 评测口径。"""
    present = [name for name in ("retrieval", "adversarial", "behavior") if isinstance(raw.get(name), dict)]
    if "retrieval" in present and "behavior" in present:
        return "full"
    if "behavior" in present:
        return "behavior"
    if "retrieval" in present and "adversarial" in present:
        return "quick"
    if "retrieval" in present:
        return "retrieval"
    if "adversarial" in present:
        return "adversarial"
    return "full"


def _judge_summary(rows: list[dict]) -> list[dict]:
    """按判定器与维度汇总逐例结论，作为批次级指标入库。"""
    buckets: dict[tuple[str, str], dict] = {}
    for row in rows:
        for verdict in row["verdicts"]:
            key = (verdict["judge"], verdict["dimension"])
            bucket = buckets.setdefault(key, {"judge": verdict["judge"], "dimension": verdict["dimension"],
                                              "passed": 0, "failed": 0, "unassessable": 0})
            bucket[verdict["status"]] += 1
    return [buckets[key] for key in sorted(buckets)]


def _layer_metrics(raw: dict, *, comparable: bool, dataset_version: str) -> dict:
    """收敛 RAG 批次指标：保留分层汇总与六项指标，剔除逐例明细避免重复存储。"""
    metrics: dict = {
        "provenance": {
            "comparable": comparable,
            "dataset_verified": comparable,
            "dataset_version": dataset_version,
            "report_git": raw.get("git") or None,
        }
    }
    for name in _RAG_LAYERS:
        layer = raw.get(name)
        if isinstance(layer, dict):
            metrics[name] = ({key: value for key, value in layer.items() if key not in _DROPPED_LAYER_KEYS}
                             if name != "six_metrics" else layer)
    algorithm = raw.get("algorithm")
    if isinstance(algorithm, dict):
        metrics["provenance"].update({
            key: algorithm[key] for key in ("judges_sha256", "model_config_sha256") if algorithm.get(key)
        })
    return metrics


def _dataset_row_fields(row: dict, *, verified: bool, catalog_version: str) -> dict:
    """把数据集登记行转换为入库字段，并标注可比性与用例索引版本。"""
    return {
        "dataset_id": row["dataset_id"],
        "name": row["name"],
        "eval_kind": row["eval_kind"],
        "version": row["version"],
        "description": row.get("description") or "",
        "source": row.get("source") or "live",
        "suite": row.get("suite") or "",
        "archived": bool(row["archived"]),
        "content_sha256": row.get("content_sha256") or "",
        "case_count": int(row.get("case_count") or 0),
        "cases_payload": row.get("cases_payload") or [],
        "metadata": row.get("metadata") or {},
        "_verified": verified,
        "_catalog_version": catalog_version,
    }


def apply_dataset_fields(run: dict, fields: dict) -> None:
    """把数据集登记字段写入待入库批次。

    Agent 与 RAG 两条报告链路共用同一份字段映射，避免两处各写一份逐字逻辑。

    Args:
        run: 待入库批次字段（原地更新）。
        fields: 数据集登记字段，需含 dataset_id、name、version，可含 description、
            suite、archived、content_sha256、case_count、cases_payload、metadata。
    """
    run.update({
        "dataset_id": fields["dataset_id"],
        "dataset_name": fields["name"],
        "dataset_version": fields["version"],
        "dataset_description": fields.get("description") or "",
        "dataset_suite": fields.get("suite") or "",
        "dataset_archived": bool(fields.get("archived")),
        "dataset_content_sha256": fields.get("content_sha256") or "",
        "dataset_case_count": int(fields.get("case_count") or 0),
        "cases_payload": fields.get("cases_payload") or [],
        "dataset_metadata": fields.get("metadata") or {},
    })


def _apply_agent_overrides(conn: Any, run: dict, *, dataset_id: str | None, dataset_version: str | None,
                           scope: str | None, mode: str | None, legacy: bool) -> None:
    """应用显式口径/方式与数据集覆盖，并校验与报告内嵌版本一致。"""
    report_mode = run["mode"]
    if mode is not None and mode != report_mode:
        raise ValueError(f"报告执行方式 {report_mode} 与指定 --mode {mode} 不一致")
    run["scope"] = scope or run.get("scope") or "full"
    if legacy:
        fields = datasets.legacy_dataset_fields("agent", AGENT_DATASET, run.get("git_sha"),
                                                source=report_mode)
        apply_dataset_fields(run, fields)
        return
    if dataset_id:
        row = evaluation_repo.dataset_row(conn, dataset_id)
        if row is None:
            raise ValueError(f"数据集未登记：{dataset_id}")
        if row["eval_kind"] != "agent":
            raise ValueError("评测类型与数据集不一致")
        if dataset_version and row["version"] != dataset_version:
            raise ValueError(f"数据集版本不一致：{row['version']} != {dataset_version}")
        computed = run.get("dataset_content_sha256") or ""
        registered = row.get("content_sha256") or ""
        if computed and registered and computed != registered:
            raise ValueError("报告内嵌数据集摘要与登记版本不一致")
        cases_payload = row.get("cases_payload") or run.get("cases_payload") or []
        fields = _dataset_row_fields(row, verified=bool(registered), catalog_version=row["version"])
        fields["cases_payload"] = cases_payload
        apply_dataset_fields(run, fields)
        return
    if dataset_version and dataset_version != run.get("dataset_version"):
        fields = datasets.dataset_fields(dataset_version, AGENT_DATASET)
        apply_dataset_fields(run, fields)


def _resolve_rag_dataset(conn: Any, raw: dict, *, dataset_id: str | None, dataset_version: str | None,
                         scope: str, mode: str, legacy: bool) -> dict:
    """解析 RAG 报告对应的数据集版本，无法验证内容摘要时归入历史版本。"""
    block = raw.get("dataset") if isinstance(raw.get("dataset"), dict) else {}
    suite = block.get("suite") or ("rag_adversarial" if scope == "adversarial" else "rag_core")
    if suite not in ("rag_core", "rag_adversarial"):
        raise ValueError(f"RAG 报告的数据集套件非法：{suite}")
    if legacy:
        report_git = raw.get("git") if isinstance(raw.get("git"), str) else ""
        row = datasets.ensure_legacy_dataset(conn, "rag", suite, report_git, source=mode)
        return _dataset_row_fields(row, verified=False, catalog_version=datasets.current_version(suite))
    version = block.get("version")
    if version:
        version = str(version)
        definition = datasets.validate_suite(version, suite)
        expected = definition.get("content_sha256") or ""
        embedded = block.get("content_sha256")
        if embedded and expected and embedded != expected:
            raise ValueError(
                f"报告内嵌数据集摘要与 {version} 描述符不一致：期望 {expected} 实际 {embedded}")
        if dataset_version and dataset_version != version:
            raise ValueError(f"报告内嵌数据集版本 {version} 与指定 --dataset-version {dataset_version} 不一致")
        if dataset_id and dataset_id != datasets.dataset_id(version, suite):
            row = evaluation_repo.dataset_row(conn, dataset_id)
            if row is None:
                raise ValueError(f"数据集未登记：{dataset_id}")
            if row["eval_kind"] != "rag" or row["suite"] != suite:
                raise ValueError("评测类型或套件与数据集不一致")
            return _dataset_row_fields(row, verified=bool(row.get("content_sha256")),
                                       catalog_version=row["version"])
        fields = datasets.dataset_fields(version, suite)
        return {**fields, "_verified": True, "_catalog_version": version}
    if dataset_id:
        row = evaluation_repo.dataset_row(conn, dataset_id)
        if row is None:
            raise ValueError(f"数据集未登记：{dataset_id}")
        if row["eval_kind"] != "rag":
            raise ValueError("评测类型与数据集不一致")
        if dataset_version and row["version"] != dataset_version:
            raise ValueError(f"数据集版本不一致：{row['version']} != {dataset_version}")
        if row["suite"] and row["suite"] != suite:
            raise ValueError(f"报告口径对应的套件 {suite} 与数据集套件 {row['suite']} 不一致")
        verified = bool(row.get("content_sha256"))
        catalog_version = row["version"] if verified else datasets.current_version(suite)
        return _dataset_row_fields(row, verified=verified, catalog_version=catalog_version)
    raise ValueError("RAG 报告未内嵌数据集版本，无法验证内容摘要；历史报告请使用 --legacy，"
                     "或显式指定 --dataset-id")


def _rag_retrieval_rows(raw: dict, cases_index: dict) -> list[dict]:
    """把 RAG 检索层逐例结果投影为统一逐例判定。

    Args:
        raw: 报告对象。
        cases_index: 用例定义索引。

    Returns:
        逐例判定列表。
    """
    layer = raw.get("retrieval") if isinstance(raw.get("retrieval"), dict) else {}
    rows = []
    for item in layer.get("per_case") or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "")
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        hit8 = _number(metrics.get("hit@8"))
        recall8 = _number(metrics.get("recall@8"))
        status = "passed" if max(hit8 or 0.0, recall8 or 0.0) > 0 else "failed"
        definition = cases_index.get(case_id)
        retrieved = [
            {key: entry.get(key) for key in ("rank", "document_id", "chunk_id", "in_expected", "score", "rerank_score")}
            for entry in (item.get("retrieved") or [])[:3] if isinstance(entry, dict)
        ]
        rows.append({
            "case_id": f"retrieval:{case_id}",
            "case_sha256": case_sha256(definition) if definition else case_sha256(
                {"id": case_id, "expected": item.get("expected_document_ids") or []}),
            "name": str(item.get("question") or "").strip()[:300],
            "category": item.get("category") or "",
            "suite": "retrieval",
            "status": status,
            "score": _score(recall8 if recall8 is not None else hit8),
            "hard_failure": False,
            "trace_id": None,
            "expected": item.get("expected_document_ids") or [],
            "observed": {"retrieved": retrieved, "metrics": metrics, "timing": item.get("timing") or {}},
            "steps_complete": None,
            "verdicts": [{
                "judge": "RetrievalKPI", "dimension": "recall", "status": status,
                "reason": f"hit@8={hit8} recall@8={recall8}", "issues": [],
            }],
        })
    return rows


def _rag_behavior_rows(raw: dict, cases_index: dict) -> list[dict]:
    """把 RAG 行为层逐例结果投影为统一逐例判定。

    Args:
        raw: 报告对象。
        cases_index: 用例定义索引。

    Returns:
        逐例判定列表。
    """
    layer = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
    rows = []
    for item in layer.get("detail") or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "")
        status = "passed" if item.get("pass") else "failed"
        definition = cases_index.get(case_id) or {}
        reason = str(item.get("why") or "").strip()
        rows.append({
            "case_id": f"behavior:{case_id}",
            "case_sha256": case_sha256(definition or {"id": case_id}),
            "name": definition.get("name") or "",
            "category": definition.get("category") or item.get("category") or "",
            "suite": "behavior",
            "status": status,
            "score": 1.0 if status == "passed" else 0.0,
            "hard_failure": False,
            "trace_id": None,
            "expected": [],
            "observed": {"why": reason},
            "steps_complete": None,
            "verdicts": [{
                "judge": "BehaviorJudge", "dimension": "answer_quality", "status": status,
                "reason": reason, "issues": [],
            }],
        })
    return rows


def _rag_adversarial_rows(raw: dict, adv_index: dict) -> list[dict]:
    """把 RAG 对抗层逐例结果投影为统一逐例判定。

    Args:
        raw: 报告对象。
        adv_index: 对抗用例定义索引。

    Returns:
        逐例判定列表。
    """
    layer = raw.get("adversarial") if isinstance(raw.get("adversarial"), dict) else {}
    rows = []
    for item in layer.get("detail") or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "")
        passed = bool(item.get("pass"))
        status = "passed" if passed else "failed"
        definition = adv_index.get(case_id) or {}
        note = str(item.get("note") or "").strip()
        rows.append({
            "case_id": f"adversarial:{case_id}",
            "case_sha256": case_sha256(definition or {"id": case_id}),
            "name": definition.get("name") or "",
            "category": definition.get("category") or "",
            "suite": "adversarial",
            "status": status,
            "score": 1.0 if passed else 0.0,
            "hard_failure": not passed,
            "trace_id": None,
            "expected": [],
            "observed": {"note": note, "e2e_s": item.get("e2e_s"), "sut_tokens": item.get("sut_tokens")},
            "steps_complete": None,
            "verdicts": [{
                "judge": "AdversarialJudge", "dimension": "governance", "status": status,
                "reason": note, "issues": [],
            }],
        })
    return rows


def _rag_gate(raw: dict) -> bool:
    """复用 RAG 运行器门禁：只有违反硬底线才算未通过。"""
    from evals.rag_eval.run import gate_check

    six = raw.get("six_metrics") if isinstance(raw.get("six_metrics"), dict) else {}
    floors, _missed = gate_check(six)
    return not floors


def _rag_report(raw: dict, payload_sha: str, dataset: dict, *, scope: str, mode: str,
                path: Path) -> tuple[dict, list[dict]]:
    """把 RAG 三层报告投影为统一批次与逐例判定。"""
    catalog_version = dataset.get("_catalog_version") or datasets.current_version("rag_core")
    cases_index = datasets.case_index(catalog_version, "rag_core")
    adv_index = datasets.case_index(catalog_version, "rag_adversarial")
    rows = [
        *_rag_retrieval_rows(raw, cases_index),
        *_rag_behavior_rows(raw, cases_index),
        *_rag_adversarial_rows(raw, adv_index),
    ]
    counts = Counter(row["status"] for row in rows)
    behavior = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
    adversarial = raw.get("adversarial") if isinstance(raw.get("adversarial"), dict) else {}
    pass_rate = _number(behavior.get("pass_rate"))
    if pass_rate is None:
        pass_rate = _number(adversarial.get("pass_rate"))
    six = raw.get("six_metrics") if isinstance(raw.get("six_metrics"), dict) else {}
    latency = six.get("latency_e2e_s") if isinstance(six.get("latency_e2e_s"), dict) else {}
    token_cost = six.get("token_cost") if isinstance(six.get("token_cost"), dict) else {}
    total_tokens = token_cost.get("total_tokens")
    if isinstance(total_tokens, bool) or not isinstance(total_tokens, int):
        total_tokens = None
    comparison = raw.get("comparison") if isinstance(raw.get("comparison"), dict) else {}
    algorithm = raw.get("algorithm") if isinstance(raw.get("algorithm"), dict) else {}
    created_at = _report_datetime(raw, path)
    run = {
        "run_id": f"rag_{created_at:%Y%m%d_%H%M%S}_{payload_sha[:8]}",
        "created_at": created_at,
        "mode": mode,
        "eval_kind": "rag",
        "profile_id": None,
        "git_sha": raw.get("git") if isinstance(raw.get("git"), str) else None,
        "dataset_sha256": dataset.get("content_sha256") or "",
        "judges_sha256": algorithm.get("judges_sha256") or "",
        "report_sha256": payload_sha,
        "baseline_sha256": comparison.get("baseline_sha256"),
        "metrics": _layer_metrics(raw, comparable=bool(dataset.get("_verified")),
                                  dataset_version=dataset["version"]),
        "gate_passed": _rag_gate(raw),
        "passed_cases": counts["passed"],
        "failed_cases": counts["failed"],
        "unassessable_cases": counts["unassessable"],
        "schema_version": int(raw["schema_version"]) if isinstance(raw.get("schema_version"), int)
        and raw["schema_version"] > 0 else 1,
        "pass_rate": round(pass_rate, 4) if pass_rate is not None else None,
        "mean_score": None,
        "latency_p50_ms": _milliseconds(latency.get("p50")),
        "latency_p95_ms": _milliseconds(latency.get("p95")),
        "total_tokens": total_tokens,
        "judges": _judge_summary(rows),
        "scope": scope,
        "dataset_version": dataset["version"],
    }
    apply_dataset_fields(run, dataset)
    return run, rows


def _agent_report(raw: dict) -> tuple[dict, list[dict]]:
    """按 Agent 既有口径投影批次与逐例判定。"""
    from evals.agent_eval.publish import project_report

    return project_report(raw)


def _store_report_object(conn: Any, tenant_id: str, run_id: str, *, eval_kind: str,
                         dataset_id: str | None, payload: bytes) -> bool:
    """把原始报告字节写入对象存储。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        run_id: 批次编号。
        eval_kind: 评测类型。
        dataset_id: 数据集编号。
        payload: 原始报告字节。

    Returns:
        是否新写入报告对象。
    """
    from taozhi.application.services import evaluation_service

    return evaluation_service.store_report(conn, tenant_id, run_id, eval_kind=eval_kind,
                                            dataset_id=dataset_id, payload=payload)


def ensure_report_object(conn: Any, tenant_id: str, run_id: str, *, eval_kind: str,
                         dataset_id: str | None, payload: bytes) -> bool:
    """幂等补写原始报告对象：已登记且摘要一致时跳过，不一致时拒绝。"""
    recorded = evaluation_repo.run_report(conn, tenant_id, run_id)
    if recorded is not None:
        if recorded["sha256"] != sha256(payload).hexdigest():
            raise ValueError(f"同一批次的原始报告内容不一致：{run_id}")
        return False
    return _store_report_object(conn, tenant_id, run_id, eval_kind=eval_kind,
                                dataset_id=dataset_id, payload=payload)


def _snapshot_suite_metadata(snapshot: dict, dataset_id: str) -> dict:
    """读取快照元数据中本次数据集所属套件的登记信息。

    Args:
        snapshot: 快照行。
        dataset_id: 本次批次的数据集编号。

    Returns:
        该数据集的套件登记元数据；快照未覆盖该数据集时返回空字典。
    """
    suites = (snapshot.get("metadata") or {}).get("suites") or []
    return next((item for item in suites if item.get("dataset_id") == dataset_id), {})


def _resolve_report_snapshot(conn: Any, tenant_id: str, owner_id: str, run: dict, rows: list[dict],
                             *, snapshot_id: str | None, run_seq: int | None) -> dict:
    """解析并校验批次的数据集内容快照，无法关联快照时拒绝入库。

    触发阶段已固化快照则直接复用；否则按该版本已登记数据集即时创建。快照必须存在、
    覆盖本次数据集且带内容摘要，逐例结果还需与快照用例一致，任何一步不满足都拒绝入库。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        owner_id: 批次发起人编号，作为即时创建快照的创建人。
        run: 待入库批次字段（含 dataset_id、dataset_version、eval_kind、scope、mode）。
        rows: 逐例判定列表，用于与快照用例比对。
        snapshot_id: 触发阶段固化的快照编号。
        run_seq: 触发阶段分配的运行序号；为空时在同一分组内顺延分配。

    Returns:
        含 dataset_snapshot_id 与 run_seq 的快照归属摘要。

    Raises:
        ValueError: 快照不存在、无法创建、缺少内容摘要，或逐例校验不通过时抛出。
    """
    if run.get("dataset_version") == datasets.LEGACY_VERSION:
        return {"dataset_snapshot_id": None, "run_seq": run_seq}
    if snapshot_id:
        snapshot = snapshots.snapshot_row(conn, tenant_id, snapshot_id)
        if snapshot is None:
            raise ValueError(f"数据集内容快照不存在：{snapshot_id}")
    else:
        try:
            snapshot = snapshots.ensure_snapshot(
                conn, tenant_id, run["eval_kind"], run["dataset_version"],
                run["dataset_id"], owner_id)
        except ValueError as exc:
            raise ValueError(
                f"数据集版本 {run['dataset_version']} 无法固化内容快照，拒绝入库：{exc}") from exc
    if not snapshot or not snapshot.get("snapshot_id"):
        raise ValueError(f"数据集版本 {run['dataset_version']} 未能生成内容快照，拒绝入库")
    expected_sha = _snapshot_suite_metadata(snapshot, run["dataset_id"]).get("content_sha256")
    if not expected_sha:
        raise ValueError(f"数据集快照缺少 {run['dataset_id']} 的内容摘要，无法校验批次归属")
    if run.get("dataset_sha256") and run["dataset_sha256"] != expected_sha:
        raise ValueError("报告数据集摘要与快照不一致，可能使用了不同版本的数据集内容")
    snapshot_cases = snapshots.snapshot_cases(conn, tenant_id, snapshot["snapshot_id"])
    snapshots.validate_report_cases(rows, snapshot_cases, run["eval_kind"],
                                    run["scope"], run["mode"])
    return {
        "dataset_snapshot_id": snapshot["snapshot_id"],
        "run_seq": run_seq or evaluation_repo.next_run_seq(
            conn, tenant_id, run["dataset_id"], run["eval_kind"],
            run["scope"], run["mode"]),
    }


def _ensure_run_profile(conn: Any, tenant_id: str, owner_id: str, run: dict, raw: dict, *,
                        profile_id: str | None, notes: str) -> None:
    """登记批次的算法版本：显式编号优先，否则按报告内嵌算法信息登记。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        owner_id: 批次发起人编号。
        run: 待入库批次字段（原地写入 profile_id）。
        raw: 报告对象，用于读取可选的 algorithm 段。
        profile_id: 显式指定的算法版本编号。
        notes: 算法版本备注。
    """
    if profile_id:
        run["profile_id"] = profiles.ensure_profile(
            conn, tenant_id, owner_id, run["eval_kind"], run["dataset_version"],
            explicit_profile_id=profile_id, notes=notes)
        return
    algorithm = raw.get("algorithm") if isinstance(raw.get("algorithm"), dict) else None
    run["profile_id"] = profiles.ensure_profile(
        conn, tenant_id, owner_id, run["eval_kind"], run["dataset_version"], notes=notes,
        algorithm=algorithm)


def _record_publish(conn: Any, tenant_id: str, owner_id: str, run: dict, rows: list[dict], *,
                    payload: bytes, legacy: bool, mark_baseline: bool, notes: str) -> bool:
    """原子写入批次、逐例判定、原始报告与审计，并按需锁定基线。

    Args:
        conn: 数据库连接（调用方事务内）。
        tenant_id: 租户编号。
        owner_id: 批次发起人编号。
        run: 已完整的批次字段。
        rows: 逐例判定列表。
        payload: 原始报告字节，用于对象存储。
        legacy: 是否为历史版本归档入库。
        mark_baseline: 新建批次后是否锁定为基线。
        notes: 入库与基线备注。

    Returns:
        新建批次返回 True，已存在同摘要批次返回 False。
    """
    created = evaluation_repo.insert_report(conn, tenant_id, owner_id, run, rows)
    if not created:
        return False
    _store_report_object(conn, tenant_id, run["run_id"], eval_kind=run["eval_kind"],
                         dataset_id=run["dataset_id"], payload=payload)
    evaluation_repo.insert_audit_log(
        conn, tenant_id, owner_id, "publish_run", run["run_id"],
        {
            "eval_kind": run["eval_kind"],
            "dataset_id": run["dataset_id"],
            "dataset_version": run["dataset_version"],
            "scope": run["scope"],
            "mode": run["mode"],
            "report_sha256": run["report_sha256"],
            "profile_id": run["profile_id"],
            "legacy": bool(legacy),
        },
    )
    if mark_baseline:
        baselines.set_baseline(conn, tenant_id, owner_id, run["run_id"],
                               notes or "报告入库时锁定基线")
    return True


def publish_report(path: Path, *, tenant_id: str, owner_id: str, kind: str = "auto",
                   profile_id: str | None = None, mark_baseline: bool = False,
                   dataset_id: str | None = None, dataset_version: str | None = None,
                   scope: str | None = None, mode: str | None = None, legacy: bool = False,
                   notes: str = "", snapshot_id: str | None = None, run_seq: int | None = None,
                   parent_run_id: str | None = None, trigger_note: str | None = None) -> dict:
    """校验报告并入库为评测批次，返回入库摘要。

    Args:
        path: 报告 JSON 文件路径。
        tenant_id: 租户编号。
        owner_id: 批次发起人编号。
        kind: 报告类型，auto 自动识别，或显式 agent/rag。
        profile_id: 显式指定的算法版本编号。
        mark_baseline: 入库成功后是否锁定为基线。
        dataset_id: 显式指定的数据集编号（Worker 触发时传入）。
        dataset_version: 显式指定的数据集版本。
        scope: 显式评测口径。
        mode: 显式执行方式。
        legacy: 是否按历史版本归档。legacy 报告内容摘要无法验证，豁免内容快照要求，
            批次以无快照、可选运行序号归档；非 legacy 报告必须绑定可校验的数据集内容快照。
        notes: 入库/基线备注。
        snapshot_id: 触发阶段固化的数据集内容快照编号。
        run_seq: 触发阶段分配的运行序号。
        parent_run_id: 重跑来源批次编号。
        trigger_note: 触发说明。

    Returns:
        含 run_id、created、gate_passed、eval_kind、dataset_id、dataset_version、scope、mode、
        report_sha256 的入库摘要。

    Raises:
        ValueError: 报告格式、数据集归属、内容摘要或数据集内容快照校验失败时抛出；
            非 legacy 报告无法关联内容快照同样拒绝入库。
    """
    path = Path(path)
    payload = path.read_bytes()
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"报告不是合法 JSON：{path.name}") from exc
    if not isinstance(raw, dict):
        raise ValueError("报告内容必须是 JSON 对象")
    if scope is not None and scope not in SCOPE_VALUES:
        raise ValueError(f"评测口径非法：{scope}")
    if mode is not None and mode not in MODE_VALUES:
        raise ValueError(f"执行方式非法：{mode}")
    resolved_kind = detect_kind(raw, kind)
    payload_sha = sha256(payload).hexdigest()
    with database_connection() as conn:
        if resolved_kind == "agent":
            run, rows = _agent_report(raw)
            _apply_agent_overrides(conn, run, dataset_id=dataset_id, dataset_version=dataset_version,
                                   scope=scope, mode=mode, legacy=legacy)
        else:
            resolved_scope = scope or (raw.get("scope") if raw.get("scope") in SCOPE_VALUES else None) \
                or infer_scope(raw)
            resolved_mode = mode or "live"
            dataset = _resolve_rag_dataset(conn, raw, dataset_id=dataset_id,
                                           dataset_version=dataset_version, scope=resolved_scope,
                                           mode=resolved_mode, legacy=legacy)
            run, rows = _rag_report(raw, payload_sha, dataset, scope=resolved_scope,
                                    mode=resolved_mode, path=path)
        if run.get("mode") not in REAL_EVAL_MODES:
            raise ValueError("评测中心只接收真实执行（live）报告，合成/回放结果不进入评测中心")
        if legacy:
            run["dataset_snapshot_id"] = None
            run["run_seq"] = run_seq
        else:
            run.update(_resolve_report_snapshot(
                conn, tenant_id, owner_id, run, rows,
                snapshot_id=snapshot_id, run_seq=run_seq))
            run["parent_run_id"] = parent_run_id or None
            run["trigger_note"] = trigger_note or None
        _ensure_run_profile(conn, tenant_id, owner_id, run, raw,
                            profile_id=profile_id, notes=notes)
        created = _record_publish(conn, tenant_id, owner_id, run, rows, payload=payload,
                                  legacy=legacy, mark_baseline=mark_baseline, notes=notes)
    return {
        "run_id": run["run_id"],
        "created": bool(created),
        "gate_passed": bool(run["gate_passed"]),
        "eval_kind": run["eval_kind"],
        "dataset_id": run["dataset_id"],
        "dataset_version": run["dataset_version"],
        "dataset_snapshot_id": run.get("dataset_snapshot_id"),
        "run_seq": run.get("run_seq"),
        "scope": run["scope"],
        "mode": run["mode"],
        "report_sha256": run["report_sha256"],
    }