"""租户范围内的评测数据集、算法版本、批次、基线、报告原文与审计读写。"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from taozhi.infrastructure.persistence.database import fetch_all, fetch_one


_RUN = """
    SELECT r.run_id, r.owner_id, u.display_name AS owner_name, r.created_at, r.mode, r.git_sha,
           r.dataset_sha256, r.judges_sha256, r.gate_passed, r.passed_cases, r.failed_cases, r.unassessable_cases,
           r.metrics->'provenance'->>'transcript_sha256' AS transcript_sha256,
           r.metrics->'provenance'->>'counterexamples_sha256' AS counterexamples_sha256,
           r.metrics->'provenance'->>'model_config_sha256' AS model_config_sha256,
           r.metrics->'provenance'->>'comparable' AS comparable_raw,
           r.passed_cases+r.failed_cases+r.unassessable_cases AS total_cases, r.metrics, r.report_sha256, r.baseline_sha256,
           r.dataset_id, d.name AS dataset_name, d.version AS dataset_version, d.archived AS dataset_archived,
           r.dataset_snapshot_id, r.run_seq, r.parent_run_id, r.trigger_note,
           r.eval_kind, r.profile_id, p.version_tag AS profile_version_tag, p.version_no AS profile_version_no,
           r.scope, r.pass_rate, r.mean_score, r.latency_p50_ms, r.latency_p95_ms,
           r.total_tokens, r.judges,
           (b.baseline_id IS NOT NULL) AS is_baseline,
           (rp.run_id IS NOT NULL) AS report_available
    FROM eval_runs r JOIN users u ON (u.tenant_id,u.id)=(r.tenant_id,r.owner_id)
    LEFT JOIN eval_datasets d ON d.dataset_id=r.dataset_id
    LEFT JOIN eval_profiles p ON p.profile_id=r.profile_id
    LEFT JOIN eval_baselines b ON b.tenant_id=r.tenant_id AND b.run_id=r.run_id AND b.active
    LEFT JOIN eval_run_reports rp ON rp.tenant_id=r.tenant_id AND rp.run_id=r.run_id
    WHERE r.tenant_id=%s
"""
_CASE = "case_id,name,category,suite,status,score,hard_failure"
_DATASET_AGG = """
    count(r.run_id) AS batch_count, max(r.created_at) AS last_run_at,
    avg(r.pass_rate) AS avg_pass_rate, max(r.pass_rate) AS best_pass_rate,
    COALESCE(sum(CASE WHEN r.gate_passed THEN 1 ELSE 0 END), 0) AS passed_batches
"""
_DATASET_COLUMNS = (
    "d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.suite, d.archived, "
    "d.content_sha256, d.case_count, d.metadata"
)


def _scope(tenant_id: str, owner_id: str | None) -> tuple[list[str], list]:
    clauses, params = [_RUN], [tenant_id]
    if owner_id is not None:
        clauses.append("AND r.owner_id=%s")
        params.append(owner_id)
    return clauses, params


def runs(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None,
         mode: str | None, gate: bool | None, dataset_id: str | None = None,
         dataset_version: str | None = None, profile_id: str | None = None,
         scope: str | None = None, algorithm_version: str | None = None,
         baseline_only: bool = False, before: tuple | None, limit: int) -> list[dict]:
    """按可见范围、评测类型、数据集、算法版本与口径筛选批次。"""
    clauses, params = _scope(tenant_id, owner_id)
    if eval_kind is not None:
        clauses.append("AND r.eval_kind=%s")
        params.append(eval_kind)
    for column, value in (("r.mode", mode), ("r.dataset_id", dataset_id),
                          ("r.dataset_version", dataset_version), ("r.profile_id", profile_id),
                          ("r.scope", scope), ("p.version_tag", algorithm_version)):
        if value is not None:
            clauses.append(f"AND {column}=%s")
            params.append(value)
    if gate is not None:
        clauses.append("AND r.gate_passed=%s")
        params.append(gate)
    if baseline_only:
        clauses.append("AND b.baseline_id IS NOT NULL")
    if before is not None:
        clauses.append("AND (r.created_at,r.run_id)<(%s,%s)")
        params.extend(before)
    clauses.append("ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def trend_runs(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None,
               dataset_version: str | None, scope: str | None, mode: str | None, limit: int) -> list[dict]:
    """按时间正序取最近批次的主指标序列，供趋势与基线差值计算。"""
    clauses, params = _scope(tenant_id, owner_id)
    if eval_kind is not None:
        clauses.append("AND r.eval_kind=%s")
        params.append(eval_kind)
    if scope is not None:
        clauses.append("AND r.scope=%s")
        params.append(scope)
    if mode is not None:
        clauses.append("AND r.mode=%s")
        params.append(mode)
    if dataset_version is not None:
        clauses.append("AND d.version=%s")
        params.append(dataset_version)
    clauses.append("ORDER BY r.created_at DESC LIMIT %s")
    rows = fetch_all(conn, " ".join(clauses), (*params, limit))
    return list(reversed(rows))


def datasets(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None,
             version: str | None = None, include_archived: bool = False) -> list[dict]:
    """按数据集版本汇总批次数量、最近批次与通过率，供评测中心目录展示。"""
    join_filter = "r.tenant_id=%s AND r.dataset_id=d.dataset_id"
    params: list[Any] = [tenant_id]
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    filters = ["true" if include_archived else "d.archived=false"]
    if eval_kind is not None:
        filters.append("d.eval_kind=%s")
        params.append(eval_kind)
    if version is not None:
        filters.append("d.version=%s")
        params.append(version)
    where = "WHERE " + " AND ".join(filters)
    return fetch_all(conn, f"""
        SELECT {_DATASET_COLUMNS}, {_DATASET_AGG}
        FROM eval_datasets d
        LEFT JOIN eval_runs r ON {join_filter}
        {where}
        GROUP BY d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.suite,
                 d.archived, d.content_sha256, d.case_count, d.metadata
        ORDER BY d.archived, max(r.created_at) DESC NULLS LAST, d.version DESC, d.dataset_id
    """, tuple(params))


def dataset_detail(conn: Any, tenant_id: str, owner_id: str | None, dataset_id: str) -> dict | None:
    """读取指定数据集版本的元数据、用例清单与批次汇总。"""
    join_filter = "r.tenant_id=%s AND r.dataset_id=d.dataset_id"
    params: list[Any] = [tenant_id]
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    params.append(dataset_id)
    return fetch_one(conn, f"""
        SELECT {_DATASET_COLUMNS}, d.cases_payload, {_DATASET_AGG}
        FROM eval_datasets d
        LEFT JOIN eval_runs r ON {join_filter}
        WHERE d.dataset_id=%s
        GROUP BY d.dataset_id, d.name, d.eval_kind, d.version, d.description, d.source, d.suite,
                 d.archived, d.content_sha256, d.case_count, d.metadata, d.cases_payload
    """, tuple(params))


def dataset_row(conn: Any, dataset_id: str) -> dict | None:
    """按主键读取数据集版本登记行（跨租户资产，用于入库与触发校验）。"""
    return fetch_one(conn, f"SELECT {_DATASET_COLUMNS}, d.cases_payload FROM eval_datasets d WHERE d.dataset_id=%s",
                     (dataset_id,))


def upsert_dataset(conn: Any, dataset: dict) -> dict:
    """幂等登记数据集版本；已有行只补齐缺失内容，不覆盖已登记描述与用例。"""
    return fetch_one(conn, """
        INSERT INTO eval_datasets(dataset_id, name, eval_kind, version, description, source, suite, archived,
                                  content_sha256, case_count, cases_payload, metadata, schema_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dataset_id) DO UPDATE SET
            case_count=GREATEST(eval_datasets.case_count, EXCLUDED.case_count),
            description=CASE WHEN eval_datasets.description='' THEN EXCLUDED.description ELSE eval_datasets.description END,
            content_sha256=CASE WHEN eval_datasets.content_sha256='' THEN EXCLUDED.content_sha256 ELSE eval_datasets.content_sha256 END,
            cases_payload=CASE WHEN jsonb_array_length(eval_datasets.cases_payload)=0
                               THEN EXCLUDED.cases_payload ELSE eval_datasets.cases_payload END,
            metadata=eval_datasets.metadata || EXCLUDED.metadata,
            updated_at=now()
        RETURNING dataset_id, name, eval_kind, version, description, source, suite, archived, content_sha256,
                  case_count, metadata
    """, (
        dataset["dataset_id"], dataset["name"], dataset["eval_kind"], dataset["version"],
        dataset.get("description") or "", dataset.get("source") or "live", dataset.get("suite") or "",
        bool(dataset.get("archived")), dataset.get("content_sha256") or "", int(dataset.get("case_count") or 0),
        Jsonb(dataset.get("cases_payload") or []), Jsonb(dataset.get("metadata") or {}),
        int(dataset.get("schema_version") or 1),
    ))


def profiles(conn: Any, tenant_id: str, owner_id: str | None, *, eval_kind: str | None = None,
             dataset_version: str | None = None) -> list[dict]:
    """列出不可变算法版本（技术栈 + 检索参数 + Prompt 快照）及关联批次聚合指标。"""
    join_filter = "r.tenant_id=p.tenant_id AND r.profile_id=p.profile_id"
    params: list[Any] = []
    if owner_id is not None:
        join_filter += " AND r.owner_id=%s"
        params.append(owner_id)
    filters = ["p.tenant_id=%s"]
    params.append(tenant_id)
    if eval_kind is not None:
        filters.append("p.eval_kind=%s")
        params.append(eval_kind)
    if dataset_version is not None:
        filters.append("r.dataset_version=%s")
        params.append(dataset_version)
    return fetch_all(conn, f"""
        SELECT p.profile_id, p.eval_kind, p.version_no, p.version_tag, p.digest, p.git_sha, p.dataset_version,
               p.tech_stack, p.retrieval_params, p.prompt_snapshots, p.prompt_sha256, p.notes, p.created_by, p.created_at,
               count(r.run_id) AS batch_count, avg(r.pass_rate) AS avg_pass_rate,
               avg(r.mean_score) AS avg_mean_score, avg(r.latency_p50_ms) AS avg_latency_p50_ms,
               avg(r.total_tokens) AS avg_tokens
        FROM eval_profiles p
        LEFT JOIN eval_runs r ON {join_filter}
        WHERE {' AND '.join(filters)}
        GROUP BY p.profile_id, p.eval_kind, p.version_no, p.version_tag, p.digest, p.git_sha, p.dataset_version,
                 p.tech_stack, p.retrieval_params, p.prompt_snapshots, p.prompt_sha256, p.notes, p.created_by, p.created_at
        ORDER BY p.version_no DESC, p.created_at DESC
    """, tuple(params))


def profile_by_id(conn: Any, tenant_id: str, profile_id: str) -> dict | None:
    """按主键查询指定租户下的算法版本。"""
    return fetch_one(conn, "SELECT * FROM eval_profiles WHERE tenant_id=%s AND profile_id=%s",
                     (tenant_id, profile_id))


def profile_by_digest(conn: Any, tenant_id: str, eval_kind: str, digest: str) -> dict | None:
    """按摘要查询已登记的算法版本，用于同一算法快照复用版本号。"""
    return fetch_one(conn, "SELECT * FROM eval_profiles WHERE tenant_id=%s AND eval_kind=%s AND digest=%s",
                     (tenant_id, eval_kind, digest))


def algorithm_digest(payload: dict[str, Any]) -> str:
    """计算算法版本摘要：代码、模型路由、检索参数与 Prompt 快照共同决定版本。"""
    canonical = json.dumps(
        {
            "git_sha": payload.get("git_sha") or "",
            "tech_stack": payload.get("tech_stack") or {},
            "retrieval_params": payload.get("retrieval_params") or {},
            "prompt_snapshots": payload.get("prompt_snapshots") or {},
        },
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def insert_profile(conn: Any, tenant_id: str, actor_id: str, payload: dict) -> dict:
    """登记算法版本：同摘要复用已有版本，显式版本标签重复则拒绝。"""
    eval_kind = payload["eval_kind"]
    digest = algorithm_digest(payload)
    existing = profile_by_digest(conn, tenant_id, eval_kind, digest)
    if existing is not None:
        return {**existing, "batch_count": 0, "avg_pass_rate": None, "avg_mean_score": None,
                "avg_latency_p50_ms": None, "avg_tokens": None, "created": False}
    conn.execute("SELECT id FROM tenants WHERE id=%s FOR UPDATE", (tenant_id,))
    version_no = fetch_one(
        conn, "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM eval_profiles WHERE tenant_id=%s AND eval_kind=%s",
        (tenant_id, eval_kind),
    )["n"]
    version_tag = payload.get("version_tag") or f"algo-v{version_no}"
    prompt_sha256 = sha256(
        json.dumps(payload.get("prompt_snapshots") or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    profile_id = f"prof_{eval_kind}_{uuid4().hex[:12]}"
    inserted = conn.execute("""
        INSERT INTO eval_profiles(
            profile_id, tenant_id, eval_kind, version_no, version_tag, digest, git_sha, dataset_version,
            tech_stack, retrieval_params, prompt_snapshots, prompt_sha256, notes, created_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, eval_kind, version_tag) DO NOTHING
        RETURNING profile_id, eval_kind, version_no, version_tag, digest, git_sha, dataset_version, tech_stack,
                  retrieval_params, prompt_snapshots, prompt_sha256, notes, created_by, created_at
    """, (
        profile_id, tenant_id, eval_kind, version_no, version_tag, digest, payload.get("git_sha"),
        payload.get("dataset_version") or "", Jsonb(payload.get("tech_stack") or {}),
        Jsonb(payload.get("retrieval_params") or {}), Jsonb(payload.get("prompt_snapshots") or {}),
        prompt_sha256, payload.get("notes") or "", actor_id,
    )).fetchone()
    if inserted is None:
        raise ValueError("该评测类型下已存在同名版本标签，历史版本不可覆盖")
    insert_audit_log(
        conn, tenant_id, actor_id, "register_profile", profile_id,
        {"eval_kind": eval_kind, "version_tag": version_tag, "version_no": version_no, "digest": digest},
    )
    return {**inserted, "batch_count": 0, "avg_pass_rate": None, "avg_mean_score": None,
            "avg_latency_p50_ms": None, "avg_tokens": None, "created": True}


def insert_audit_log(conn: Any, tenant_id: str, actor_id: str, action: str, target_id: str,
                     summary: dict[str, Any], *, git_sha: str | None = None) -> str:
    """记录受控写入操作的审计留痕。"""
    audit_id = f"aud_{uuid4().hex[:16]}"
    conn.execute("""
        INSERT INTO eval_audit_logs(audit_id, tenant_id, actor_id, action, target_id, git_sha, summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (audit_id, tenant_id, actor_id, action, target_id, git_sha, Jsonb(summary)))
    return audit_id


def audit_logs(conn: Any, tenant_id: str, *, action: str | None, before: tuple | None, limit: int) -> list[dict]:
    """按租户读取受控写入审计留痕，时间倒序分页。"""
    clauses = ["SELECT audit_id, tenant_id, actor_id, action, target_id, git_sha, summary, created_at "
               "FROM eval_audit_logs WHERE tenant_id=%s"]
    params: list[Any] = [tenant_id]
    if action is not None:
        clauses.append("AND action=%s")
        params.append(action)
    if before is not None:
        clauses.append("AND (created_at,audit_id)<(%s,%s)")
        params.extend(before)
    clauses.append("ORDER BY created_at DESC, audit_id DESC LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def baselines(conn: Any, tenant_id: str, *, eval_kind: str | None = None, dataset_version: str | None = None,
              include_history: bool = True) -> list[dict]:
    """列出 active 与历史基线，附带对应批次的指标与算法版本。"""
    clauses = ["""
        SELECT b.baseline_id, b.eval_kind, b.dataset_id, b.dataset_version, b.scope, b.mode, b.run_id,
               b.note, b.created_by, b.created_at, b.active, b.superseded_at,
               r.created_at AS run_created_at, r.gate_passed AS run_gate_passed, r.pass_rate AS run_pass_rate,
               r.mode AS run_mode, r.git_sha AS run_git_sha, r.report_sha256 AS run_report_sha256,
               p.version_tag AS algorithm_version
        FROM eval_baselines b
        JOIN eval_runs r ON (r.tenant_id,r.run_id)=(b.tenant_id,b.run_id)
        LEFT JOIN eval_profiles p ON p.profile_id=r.profile_id
        WHERE b.tenant_id=%s
    """]
    params: list[Any] = [tenant_id]
    if eval_kind is not None:
        clauses.append("AND b.eval_kind=%s")
        params.append(eval_kind)
    if dataset_version is not None:
        clauses.append("AND b.dataset_version=%s")
        params.append(dataset_version)
    if not include_history:
        clauses.append("AND b.active")
    clauses.append("ORDER BY b.active DESC, b.created_at DESC LIMIT 200")
    return fetch_all(conn, " ".join(clauses), tuple(params))


def active_baseline(conn: Any, tenant_id: str, *, eval_kind: str, dataset_version: str,
                    scope: str, mode: str) -> dict | None:
    """读取某数据集版本 + 口径 + 方式的唯一 active 基线批次。"""
    return fetch_one(conn, """
        SELECT b.baseline_id, b.note, b.created_at AS baseline_created_at, r.*
        FROM eval_baselines b JOIN eval_runs r ON (r.tenant_id,r.run_id)=(b.tenant_id,b.run_id)
        WHERE b.tenant_id=%s AND b.eval_kind=%s AND b.dataset_version=%s AND b.scope=%s AND b.mode=%s AND b.active
    """, (tenant_id, eval_kind, dataset_version, scope, mode))


def set_baseline(conn: Any, tenant_id: str, actor_id: str, *, run: dict, note: str) -> dict:
    """把指定批次设为该数据集版本 + 口径 + 方式的 active 基线，旧基线保留历史。"""
    conn.execute("""
        UPDATE eval_baselines SET active=false, superseded_at=now()
        WHERE tenant_id=%s AND eval_kind=%s AND dataset_version=%s AND scope=%s AND mode=%s AND active
    """, (tenant_id, run["eval_kind"], run["dataset_version"], run["scope"], run["mode"]))
    baseline_id = f"base_{run['eval_kind']}_{uuid4().hex[:12]}"
    row = fetch_one(conn, """
        INSERT INTO eval_baselines(baseline_id, tenant_id, eval_kind, dataset_id, dataset_version, scope, mode,
                                   run_id, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING baseline_id, eval_kind, dataset_id, dataset_version, scope, mode, run_id, note, created_by,
                  created_at, active
    """, (baseline_id, tenant_id, run["eval_kind"], run["dataset_id"], run["dataset_version"],
          run["scope"], run["mode"], run["run_id"], note, actor_id))
    insert_audit_log(
        conn, tenant_id, actor_id, "set_baseline", run["run_id"],
        {"baseline_id": baseline_id, "dataset_id": run["dataset_id"], "dataset_version": run["dataset_version"],
         "scope": run["scope"], "mode": run["mode"], "note": note},
    )
    return row


def insert_run_report(conn: Any, tenant_id: str, run_id: str, *, object_key: str, digest: str,
                      byte_size: int, content_type: str = "application/json") -> bool:
    """登记原始报告对象元数据；重复登记内容不一致时拒绝。"""
    existing = fetch_one(conn, "SELECT sha256 FROM eval_run_reports WHERE tenant_id=%s AND run_id=%s",
                         (tenant_id, run_id))
    if existing is not None:
        if existing["sha256"] != digest:
            raise ValueError("同一批次的原始报告内容不一致，拒绝覆盖")
        return False
    conn.execute("""
        INSERT INTO eval_run_reports(tenant_id, run_id, object_key, sha256, byte_size, content_type)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (tenant_id, run_id, object_key, digest, byte_size, content_type))
    return True


def run_report(conn: Any, tenant_id: str, run_id: str) -> dict | None:
    """读取批次原始报告的对象位置与校验摘要。"""
    return fetch_one(conn, "SELECT object_key, sha256, byte_size, content_type FROM eval_run_reports "
                           "WHERE tenant_id=%s AND run_id=%s", (tenant_id, run_id))


def run(conn: Any, tenant_id: str, owner_id: str | None, *, run_id: str | None = None,
        report_digest: str | None = None) -> dict | None:
    clauses, params = _scope(tenant_id, owner_id)
    if run_id is not None:
        clauses.append("AND r.run_id=%s")
        params.append(run_id)
    else:
        clauses.append("AND r.report_sha256=%s")
        params.append(report_digest)
    return fetch_one(conn, " ".join(clauses), tuple(params))


def run_by_id(conn: Any, tenant_id: str, run_id: str) -> dict | None:
    """按租户读取批次（不做发起人可见范围过滤，供后台任务使用）。"""
    return fetch_one(conn, " ".join([_RUN, "AND r.run_id=%s"]), (tenant_id, run_id))


def dataset_row_lock(conn: Any, dataset_id: str) -> None:
    """锁定数据集登记行，串行化同一数据集的运行序号分配。"""
    conn.execute("SELECT 1 FROM eval_datasets WHERE dataset_id=%s FOR UPDATE", (dataset_id,))


def next_run_seq(conn: Any, tenant_id: str, dataset_id: str, eval_kind: str, scope: str,
                 mode: str) -> int:
    """分配同一分组内的下一个运行序号（调用方负责事务与并发锁）。"""
    row = fetch_one(conn, """
        SELECT COALESCE(MAX(run_seq), 0) + 1 AS next_seq
        FROM eval_runs
        WHERE tenant_id=%s AND dataset_id=%s AND eval_kind=%s AND scope=%s AND mode=%s
    """, (tenant_id, dataset_id, eval_kind, scope, mode))
    return int(row["next_seq"])


def cases(conn: Any, tenant_id: str, run_id: str, *, status: str | None, suite: str | None,
          after: str | None, limit: int) -> list[dict]:
    """按状态与用例分组读取批次逐例判定，case_id 正序分页。"""
    clauses = [f"SELECT {_CASE} FROM eval_case_results WHERE tenant_id=%s AND run_id=%s"]
    params: list[Any] = [tenant_id, run_id]
    if status is not None:
        clauses.append("AND status=%s")
        params.append(status)
    if suite is not None:
        clauses.append("AND suite=%s")
        params.append(suite)
    if after is not None:
        clauses.append("AND case_id>%s")
        params.append(after)
    clauses.append("ORDER BY case_id LIMIT %s")
    return fetch_all(conn, " ".join(clauses), (*params, limit))


def case_detail(conn: Any, tenant_id: str, run_id: str, case_id: str) -> dict | None:
    return fetch_one(conn, f"SELECT {_CASE},expected,observed,steps_complete,verdicts,trace_id FROM eval_case_results "
                     "WHERE tenant_id=%s AND run_id=%s AND case_id=%s", (tenant_id, run_id, case_id))


def comparison_rows(conn: Any, tenant_id: str, run_id: str) -> list[dict]:
    return fetch_all(conn, "SELECT case_id,case_sha256,suite,status FROM eval_case_results "
                     "WHERE tenant_id=%s AND run_id=%s ORDER BY case_id", (tenant_id, run_id))


def trace_link(conn: Any, tenant_id: str, owner_id: str | None, trace_id: str) -> dict | None:
    sql = ("SELECT DISTINCT j.id AS task_id, s.trace_id FROM task_spans s "
           "JOIN jobs j ON (j.tenant_id,j.id)=(s.tenant_id,s.job_id) "
           "WHERE s.tenant_id=%s AND s.trace_id=%s AND j.kind IN ('ask','research')")
    params = [tenant_id, trace_id]
    if owner_id is not None:
        sql += " AND j.owner_id=%s"
        params.append(owner_id)
    rows = fetch_all(conn, sql + " LIMIT 2", tuple(params))
    return rows[0] if len(rows) == 1 else None


def retire_dataset(conn: Any, dataset_id: str) -> None:
    """把旧数据集版本标记为归档（仍保留历史批次与报告）。"""
    conn.execute("UPDATE eval_datasets SET archived=true, updated_at=now() WHERE dataset_id=%s", (dataset_id,))


def move_runs_to_dataset(conn: Any, old_dataset_id: str, new_dataset_id: str, dataset_version: str) -> int:
    """把旧数据集下的历史批次迁移到统一版本数据集，保留批次内容与报告。"""
    rows = fetch_all(conn, "UPDATE eval_runs SET dataset_id=%s, dataset_version=%s WHERE dataset_id=%s RETURNING run_id",
                     (new_dataset_id, dataset_version, old_dataset_id))
    return len(rows)


def insert_report(conn: Any, tenant_id: str, owner_id: str, report: dict, rows: list[dict]) -> bool:
    """在调用者的事务中原子导入批次、逐例判定与数据集登记，拒绝覆盖或更换归属。"""
    if not fetch_one(conn, "SELECT id FROM users WHERE tenant_id=%s AND id=%s", (tenant_id, owner_id)):
        raise ValueError("评测发起人不属于指定租户")
    eval_kind = report.get("eval_kind", "agent")
    dataset_id = report["dataset_id"]
    dataset_version = report.get("dataset_version") or "v1"
    upsert_dataset(conn, {
        "dataset_id": dataset_id,
        "name": report["dataset_name"],
        "eval_kind": eval_kind,
        "version": dataset_version,
        "description": report.get("dataset_description") or "",
        "source": report["mode"],
        "suite": report.get("dataset_suite") or "",
        "archived": bool(report.get("dataset_archived")),
        "content_sha256": report.get("dataset_content_sha256") or "",
        "case_count": report.get("dataset_case_count") or (
            report["passed_cases"] + report["failed_cases"] + report["unassessable_cases"]),
        "cases_payload": report.get("cases_payload") or [],
        "metadata": report.get("dataset_metadata") or {},
        "schema_version": report["schema_version"],
    })
    target = {
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        **report,
        "eval_kind": eval_kind,
        "dataset_version": dataset_version,
        "scope": report.get("scope") or "full",
        "profile_id": report.get("profile_id"),
        "dataset_snapshot_id": report.get("dataset_snapshot_id"),
        "run_seq": report.get("run_seq"),
        "parent_run_id": report.get("parent_run_id"),
        "trigger_note": report.get("trigger_note"),
        "metrics": Jsonb(report["metrics"]),
        "judges": Jsonb(report["judges"]),
    }
    inserted = conn.execute("""
        INSERT INTO eval_runs(tenant_id,run_id,owner_id,created_at,mode,eval_kind,profile_id,git_sha,dataset_sha256,judges_sha256,
                              report_sha256,baseline_sha256,metrics,gate_passed,passed_cases,failed_cases,unassessable_cases,
                              dataset_id,dataset_version,scope,pass_rate,mean_score,latency_p50_ms,latency_p95_ms,total_tokens,judges,
                              dataset_snapshot_id,run_seq,parent_run_id,trigger_note)
        VALUES (%(tenant_id)s,%(run_id)s,%(owner_id)s,%(created_at)s,%(mode)s,%(eval_kind)s,%(profile_id)s,%(git_sha)s,%(dataset_sha256)s,
                %(judges_sha256)s,%(report_sha256)s,%(baseline_sha256)s,%(metrics)s,%(gate_passed)s,%(passed_cases)s,
                %(failed_cases)s,%(unassessable_cases)s,%(dataset_id)s,%(dataset_version)s,%(scope)s,%(pass_rate)s,%(mean_score)s,
                %(latency_p50_ms)s,%(latency_p95_ms)s,%(total_tokens)s,%(judges)s,
                %(dataset_snapshot_id)s,%(run_seq)s,%(parent_run_id)s,%(trigger_note)s)
        ON CONFLICT (tenant_id,run_id) DO NOTHING RETURNING run_id
    """, target).fetchone()
    if inserted is None:
        existing = run(conn, tenant_id, owner_id, run_id=report["run_id"])
        if existing is None or existing["report_sha256"] != report["report_sha256"]:
            raise ValueError("同一批次编号已经归属于其他发起人或对应不同内容")
        conn.execute("""
            UPDATE eval_runs SET dataset_id=COALESCE(dataset_id,%s),
                   dataset_version=CASE WHEN dataset_version='' THEN %s ELSE dataset_version END,
                   scope=CASE WHEN scope='' THEN %s ELSE scope END,
                   pass_rate=COALESCE(pass_rate,%s), mean_score=COALESCE(mean_score,%s),
                   latency_p50_ms=COALESCE(latency_p50_ms,%s), latency_p95_ms=COALESCE(latency_p95_ms,%s),
                   total_tokens=COALESCE(total_tokens,%s), judges=COALESCE(judges,%s)
            WHERE tenant_id=%s AND run_id=%s
        """, (report["dataset_id"], dataset_version, target["scope"], report["pass_rate"], report["mean_score"],
              report["latency_p50_ms"], report["latency_p95_ms"], report["total_tokens"], Jsonb(report["judges"]),
              tenant_id, report["run_id"]))
        return False
    with conn.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO eval_case_results(tenant_id,run_id,case_id,case_sha256,name,category,suite,status,score,
                                          hard_failure,trace_id,expected,observed,steps_complete,verdicts)
            VALUES (%(tenant_id)s,%(run_id)s,%(case_id)s,%(case_sha256)s,%(name)s,%(category)s,%(suite)s,%(status)s,
                    %(score)s,%(hard_failure)s,%(trace_id)s,%(expected)s,%(observed)s,%(steps_complete)s,%(verdicts)s)
        """, ({
            **row, "tenant_id": tenant_id, "run_id": report["run_id"],
            "expected": Jsonb(row["expected"]),
            "observed": Jsonb(row["observed"]) if row["observed"] is not None else None,
            "verdicts": Jsonb(row["verdicts"]),
        } for row in rows))
    return True