"""任务、事件流、模型调用账本与执行租约仓储。"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from typing import Any

from taozhi.config import load_config
from taozhi.domain.access import can_review, can_task
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence.access_repo import audit, sources_valid
from taozhi.knowledge.db import as_jsonb, database_connection, fetch_all, fetch_one


def generate_id(prefix: str) -> str:
    """生成带业务前缀的全局唯一随机标识。"""
    return prefix + "_" + secrets.token_hex(12)


def get_job(conn: Any, tenant_id: str, job_id: str, *, for_update: bool = False) -> dict | None:
    """按租户与任务编号读取任务记录。"""
    suffix = " FOR UPDATE" if for_update else ""
    return fetch_one(conn, f"SELECT * FROM jobs WHERE tenant_id=%s AND id=%s{suffix}", (tenant_id, job_id))


def get_job_by_id(conn: Any, job_id: str, *, for_update: bool = False) -> dict | None:
    """按任务编号读取任务记录。"""
    suffix = " FOR UPDATE" if for_update else ""
    return fetch_one(conn, f"SELECT * FROM jobs WHERE id=%s{suffix}", (job_id,))


def list_ask_and_research_jobs(conn: Any, tenant_id: str, limit: int = 500) -> list[dict]:
    """按时间倒序列出问答与调研任务。"""
    return fetch_all(
        conn,
        "SELECT * FROM jobs WHERE tenant_id=%s AND kind IN ('ask','research') ORDER BY created_at DESC LIMIT %s",
        (tenant_id, limit),
    )


def list_ask_jobs(conn: Any, tenant_id: str, limit: int = 2000) -> list[dict]:
    """按时间倒序列出租户问答任务（用于工作台概览统计，设置上限防止全表无限加载）。"""
    return fetch_all(
        conn,
        "SELECT * FROM jobs WHERE tenant_id=%s AND kind='ask' ORDER BY created_at DESC LIMIT %s",
        (tenant_id, limit),
    )


def count_jobs_by_state(conn: Any, tenant_id: str) -> list[dict]:
    """按任务状态聚合数量。"""
    return fetch_all(conn, "SELECT state,count(*) AS count FROM jobs WHERE tenant_id=%s GROUP BY state", (tenant_id,))


def create_job(
    conn: Any,
    p: Any,
    kind: str,
    node: str,
    payload: dict,
    question: str = "",
    level: int = 1,
    key: str | None = None,
    conversation_id: str = "",
) -> dict:
    """创建异步任务并在启用 Kafka 时同事务写入 Outbox。"""
    if node not in p.nodes or (
        not p.boss
        and not any(
            p.overlaps(m["node_id"], node)
            and not (m["role"] == "employee" and p.nodes.get(m["node_id"], {}).get("kind") == "company" and node != m["node_id"])
            for m in p.memberships
        )
        and node != p.root
    ):
        raise Denied("不属于该组织范围")
    fingerprint = hashlib.sha256(
        json.dumps(
            dict(kind=kind, node=node, payload=payload, question=question, level=level, conversation=conversation_id),
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if key:
        existing = fetch_one(conn, "SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND idempotency_key=%s", (p.tenant_id, p.id, key))
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise Denied("重复提交标识对应不同内容", "IDEMPOTENCY_CONFLICT", 409)
            return existing
    conn.execute("SELECT id FROM tenants WHERE id=%s FOR UPDATE", (p.tenant_id,))
    if key:
        existing = fetch_one(conn, "SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND idempotency_key=%s", (p.tenant_id, p.id, key))
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise Denied("重复提交标识对应不同内容", "IDEMPOTENCY_CONFLICT", 409)
            return existing
    count = fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE tenant_id=%s AND state IN ('queued','running')", (p.tenant_id,))["n"]
    cfg = load_config()
    if count >= cfg.max_pending:
        raise Denied("任务较多，请稍后重试", "QUEUE_FULL", 429)
    job = fetch_one(
        conn,
        "INSERT INTO jobs(id,tenant_id,owner_id,owner_version,node_id,kind,question,input_level,conversation_id,payload,idempotency_key,fingerprint) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (generate_id("task"), p.tenant_id, p.id, p.version, node, kind, question, level, conversation_id, as_jsonb(payload), key, fingerprint),
    )
    if cfg.kafka_enabled:
        conn.execute("INSERT INTO task_outbox(job_id) VALUES(%s)", (job["id"],))
    audit(conn, p, "提交问答" if kind == "ask" else "提交资料解析", job["id"], node, level, detail={"kind": kind})
    return job


def list_task_events(conn: Any, tenant_id: str, job_id: str, after_id: int | None = None) -> list[dict]:
    """读取任务执行事件流：传 after_id 时返回游标后的增量事件，否则返回含时间戳的全量历史。"""
    if after_id is not None:
        return fetch_all(
            conn,
            "SELECT id,stage,progress,status FROM task_events WHERE tenant_id=%s AND job_id=%s AND id>%s ORDER BY id",
            (tenant_id, job_id, after_id),
        )
    return fetch_all(
        conn,
        "SELECT id,stage,progress,status,created_at FROM task_events WHERE tenant_id=%s AND job_id=%s ORDER BY id ASC",
        (tenant_id, job_id),
    )


def update_job_stage(conn: Any, tenant_id: str, job_id: str, run_version: int, stage: str, progress: int, status: str = "running") -> None:
    """更新任务进度与追加事件流记录。"""
    conn.execute("UPDATE jobs SET stage=%s,progress=%s WHERE id=%s AND run_version=%s", (stage, progress, job_id, run_version))
    conn.execute(
        "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,%s,%s)",
        (tenant_id, job_id, run_version, stage, progress, status),
    )


def complete_job(
    conn: Any,
    *,
    tenant_id: str,
    job_id: str,
    run_version: int,
    state: str,
    stage: str,
    result: Any = None,
    sources: Any = None,
    input_level: int | None = None,
) -> None:
    """完成任务并写入结果与终态事件。"""
    if result is None and sources is None and input_level is None:
        conn.execute(
            "UPDATE jobs SET state=%s,progress=100,stage=%s,finished_at=now(),lease_until=NULL WHERE id=%s AND run_version=%s",
            (state, stage, job_id, run_version),
        )
    elif input_level is not None:
        conn.execute(
            "UPDATE jobs SET result=%s,sources=%s,input_level=%s,state=%s,stage=%s,progress=100,lease_until=NULL,finished_at=now() WHERE id=%s AND run_version=%s",
            (as_jsonb(result), as_jsonb(sources if sources is not None else []), input_level, state, stage, job_id, run_version),
        )
    else:
        conn.execute(
            "UPDATE jobs SET result=%s,sources=%s,state=%s,stage=%s,progress=100,lease_until=NULL,finished_at=now() WHERE id=%s AND run_version=%s",
            (as_jsonb(result), as_jsonb(sources if sources is not None else []), state, stage, job_id, run_version),
        )
    conn.execute(
        "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,100,%s)",
        (tenant_id, job_id, run_version, stage, state),
    )


def mark_job_failed_or_cancelled(conn: Any, job: dict, status: str, message: str) -> None:
    """将执行中失败或被取消的任务落库终态。"""
    stage_txt = "已取消" if status == "cancelled" else "处理失败"
    conn.execute(
        "UPDATE jobs SET state=%s,error=%s,stage=%s,finished_at=now(),lease_until=NULL WHERE id=%s",
        (status, message, stage_txt, job["id"]),
    )
    conn.execute(
        "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,100,%s)",
        (job["tenant_id"], job["id"], job["run_version"], message, status),
    )


def expire_stale_reviews(conn: Any = None) -> int:
    """清理超期未审核任务，避免 review / interrupt 挂起状态无限期滞留。"""
    ttl = int(load_config().review_timeout)
    if ttl <= 0:
        return 0

    def _expire(c: Any) -> int:
        stale = fetch_all(
            c,
            "UPDATE jobs SET state='cancelled',error='审核等待超时，任务已自动关闭',stage='审核已超时关闭',finished_at=now(),lease_until=NULL "
            "WHERE state='review' AND finished_at IS NOT NULL AND finished_at<now()-(%s*interval '1 second') "
            "RETURNING id,tenant_id,run_version",
            (ttl,),
        )
        for row in stale:
            c.execute(
                "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,'审核等待超时，任务已自动关闭',100,'cancelled')",
                (row["tenant_id"], row["id"], row["run_version"]),
            )
        return len(stale)

    if conn is not None:
        return _expire(conn)
    with database_connection() as c:
        return _expire(c)


def claim_next_job(worker_id: str | None = None) -> dict | None:
    """认领下一个可执行任务（含租约过期抢占与看门狗清理）。"""
    wid = worker_id or f"worker_{secrets.token_hex(8)}"
    cfg = load_config()
    with database_connection() as c:
        expire_stale_reviews(c)
        c.execute(
            "UPDATE jobs SET state='failed',error='任务重试次数已达上限',finished_at=now() "
            "WHERE state IN ('queued','running') AND attempts>=%s AND (lease_until IS NULL OR lease_until<now())",
            (cfg.max_attempts,),
        )
        row = fetch_one(
            c,
            "SELECT * FROM jobs WHERE NOT cancel_requested AND attempts<%s AND (state='queued' OR (state='running' AND lease_until<now())) "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
            (cfg.max_attempts,),
        )
        if not row:
            return None
        return fetch_one(
            c,
            "UPDATE jobs SET state='running',run_version=run_version+1,attempts=attempts+1,worker_id=%s,"
            "lease_until=now()+(%s*interval '1 second'),started_at=COALESCE(started_at,now()),error='' WHERE id=%s RETURNING *",
            (wid, cfg.lease_seconds, row["id"]),
        )


def claim_job_by_id(job_id: str, worker_id: str | None = None) -> dict | None:
    """按 Kafka 指定任务编号幂等抢占排队或租约已过期的任务。

    Args:
        job_id: 任务主键标识。
        worker_id: 认领该任务的 Worker 标识。

    Returns:
        成功抢占后的任务记录；若任务已被其他节点领走或终止则返回 None。
    """
    wid = worker_id or f"worker_{secrets.token_hex(8)}"
    cfg = load_config()
    with database_connection() as c:
        row = fetch_one(
            c,
            "SELECT id FROM jobs WHERE id=%s AND NOT cancel_requested AND attempts<%s "
            "AND (state='queued' OR (state='running' AND lease_until<now())) FOR UPDATE SKIP LOCKED",
            (job_id, cfg.max_attempts),
        )
        if not row:
            return None
        return fetch_one(
            c,
            "UPDATE jobs SET state='running',run_version=run_version+1,attempts=attempts+1,worker_id=%s,"
            "lease_until=now()+(%s*interval '1 second'),started_at=COALESCE(started_at,now()),error='' WHERE id=%s RETURNING *",
            (wid, cfg.lease_seconds, row["id"]),
        )


def delete_checkpoint_threads(conn: Any, thread_patterns: list[str]) -> None:
    """按 thread_id 模式批量清理 LangGraph PostgresSaver 三张检查点表。"""
    if not thread_patterns:
        return
    for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        exists = fetch_one(conn, "SELECT to_regclass(%s) AS tbl", (tbl,))
        if exists and exists.get("tbl"):
            conn.execute(f"DELETE FROM {tbl} WHERE thread_id LIKE ANY(%s)", (thread_patterns,))


def prune_expired_runtime_records(conn: Any) -> None:
    """回收超过 24 小时的离线心跳、过期/已吊销会话、登录锁定与已终结单轮问答检查点。"""
    conn.execute("DELETE FROM worker_heartbeats WHERE updated_at<now()-interval '1 day'")
    conn.execute("DELETE FROM sessions WHERE revoked OR expires_at<now()-interval '1 day'")
    conn.execute("DELETE FROM login_attempts WHERE updated_at<now()-interval '1 day'")
    for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        exists = fetch_one(conn, "SELECT to_regclass(%s) AS tbl", (tbl,))
        if exists and exists.get("tbl"):
            conn.execute(
                f"DELETE FROM {tbl} WHERE thread_id LIKE %s ESCAPE '\\' "
                "AND split_part(thread_id, ':', 1) IN ("
                "SELECT id FROM jobs WHERE kind='ask' AND state IN ('completed','failed','cancelled','rejected')"
                ")",
                (r"task\_%:%",),
            )


_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE_AT = 0.0


def touch_worker_heartbeat(conn: Any, worker_id: str, prune: bool = False) -> None:
    """刷新 Worker 心跳；开启 prune 时按 5 分钟节流窗口自动执行运行期垃圾回收。"""
    global _LAST_PRUNE_AT
    conn.execute("INSERT INTO worker_heartbeats(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET updated_at=now()", (worker_id,))
    if not prune:
        return
    now_mono = time.monotonic()
    with _PRUNE_LOCK:
        if now_mono - _LAST_PRUNE_AT < 300.0:
            return
        _LAST_PRUNE_AT = now_mono
    prune_expired_runtime_records(conn)


def renew_lease_once(conn: Any, job_id: str, run_version: int, worker_id: str, lease_seconds: int) -> None:
    """续约单次正在执行的任务租约时间。"""
    touch_worker_heartbeat(conn, worker_id)
    conn.execute(
        "UPDATE jobs SET lease_until=now()+(%s*interval '1 second') WHERE id=%s AND run_version=%s AND worker_id=%s AND state='running'",
        (lease_seconds, job_id, run_version, worker_id),
    )


def count_active_workers(conn: Any) -> int:
    """统计最近 30 秒内活跃 Worker 数量。"""
    return fetch_one(conn, "SELECT count(*) AS count FROM worker_heartbeats WHERE updated_at>now()-interval '30 seconds'")["count"]


def count_running_workers(conn: Any) -> int:
    """统计持有有效租约的执行中任务数。"""
    return fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE state='running' AND lease_until>now()")["n"]


def count_model_calls(conn: Any, job_id: str) -> int:
    """统计指定任务已发生的外部模型调用次数。"""
    return fetch_one(conn, "SELECT count(*) AS n FROM model_calls WHERE job_id=%s", (job_id,))["n"]


def insert_model_call(conn: Any, tenant_id: str, job_id: str, role: str) -> str | None:
    """登记外部模型调用开始占位记录。"""
    row = fetch_one(
        conn,
        "INSERT INTO model_calls(tenant_id,job_id,role,elapsed_ms,outcome) VALUES(%s,%s,%s,0,'started') RETURNING id",
        (tenant_id, job_id, role),
    )
    return row["id"] if row else None


def update_model_call(conn: Any, call_id: str, elapsed_ms: int, prompt_tokens: int, completion_tokens: int, outcome: str) -> None:
    """回写模型调用真实耗时、Token 消耗与结果状态。"""
    conn.execute(
        "UPDATE model_calls SET elapsed_ms=%s,prompt_tokens=%s,completion_tokens=%s,outcome=%s WHERE id=%s",
        (elapsed_ms, prompt_tokens, completion_tokens, outcome, call_id),
    )


def last_conversation_question(conn: Any, tenant_id: str, owner_id: str, conversation_id: str, exclude_job_id: str) -> str | None:
    """读取同一会话中上一条已完成的提问，供短指代词多轮检索补全。"""
    row = fetch_one(
        conn,
        "SELECT question FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='ask' AND state='completed' "
        "AND conversation_id=%s AND id != %s ORDER BY created_at DESC LIMIT 1",
        (tenant_id, owner_id, conversation_id, exclude_job_id),
    )
    return str(row["question"]) if row and row.get("question") else None


def list_recent_completed_ask(conn: Any, tenant_id: str, owner_id: str, limit: int = 3) -> list[dict]:
    """读取用户最近已完成的问答结果与来源列表。"""
    return fetch_all(
        conn,
        "SELECT result,sources,input_level FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='ask' "
        "AND state='completed' ORDER BY created_at DESC LIMIT %s",
        (tenant_id, owner_id, limit),
    )


def list_research_conversation_summaries(conn: Any, tenant_id: str, owner_id: str) -> list[dict]:
    """按会话分组列出市场调研对话概览。"""
    return fetch_all(
        conn,
        "SELECT conversation_id, min(created_at) as started_at, max(created_at) as last_active, "
        "count(*) as turns, (array_agg(question ORDER BY created_at ASC))[1] as title, "
        "(array_agg(payload ORDER BY created_at ASC))[1] as first_payload, "
        "(array_agg(state ORDER BY created_at DESC))[1] as latest_state "
        "FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id!='' "
        "GROUP BY conversation_id ORDER BY last_active DESC LIMIT 100",
        (tenant_id, owner_id),
    )


def list_research_conversation_turns(conn: Any, tenant_id: str, owner_id: str, conversation_id: str) -> list[dict]:
    """按创建时间顺序读取单个调研会话下的全部轮次。"""
    return fetch_all(
        conn,
        "SELECT * FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id=%s ORDER BY created_at ASC",
        (tenant_id, owner_id, conversation_id),
    )


def delete_research_conversation_records(conn: Any, tenant_id: str, owner_id: str, conversation_id: str) -> None:
    """级联删除个人调研会话下全部任务、外键子表及对应 LangGraph 检查点记录。

    Args:
        conn: 事务中的数据库连接。
        tenant_id: 所属租户标识。
        owner_id: 会话所属用户标识。
        conversation_id: 待删除的调研会话编号。
    """
    rows = fetch_all(
        conn,
        "SELECT id FROM jobs WHERE tenant_id=%s AND owner_id=%s AND kind='research' AND conversation_id=%s",
        (tenant_id, owner_id, conversation_id),
    )
    job_ids = [r["id"] for r in rows]
    if not job_ids:
        return
    conn.execute("DELETE FROM task_reviews WHERE job_id = ANY(%s)", (job_ids,))
    conn.execute("DELETE FROM model_calls WHERE job_id = ANY(%s)", (job_ids,))
    conn.execute("DELETE FROM task_events WHERE tenant_id=%s AND job_id = ANY(%s)", (tenant_id, job_ids))
    conn.execute("DELETE FROM jobs WHERE tenant_id=%s AND id = ANY(%s)", (tenant_id, job_ids))

    # 同步清理 LangGraph PostgresSaver 遗留检查点，防止长期增长
    delete_checkpoint_threads(conn, [f"research_{jid}_%" for jid in job_ids])


def mark_job_cancelled(conn: Any, job_id: str) -> None:
    """标记任务取消请求，非运行中任务直接置为已取消。"""
    conn.execute(
        "UPDATE jobs SET cancel_requested=true,state=CASE WHEN state='running' THEN state ELSE 'cancelled' END WHERE id=%s",
        (job_id,),
    )


def append_steering_to_job(conn: Any, tenant_id: str, job_id: str, run_ver: int, progress: int, steer_id: str, text: str) -> None:
    """向运行中任务的 JSON payload 追加幂等干预指令并写入事件流。"""
    stage_desc = f"【用户中途追加干预指令】：{text}"
    conn.execute(
        "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,%s,'running')",
        (tenant_id, job_id, run_ver, stage_desc, progress),
    )
    conn.execute(
        """UPDATE jobs SET payload = jsonb_set(
            COALESCE(payload, '{}'::jsonb),
            '{steering_inputs}',
            COALESCE(payload->'steering_inputs', '[]'::jsonb) || jsonb_build_array(%s)
        ) WHERE id=%s""",
        (as_jsonb({"id": steer_id, "text": text}), job_id),
    )


def requeue_job(conn: Any, tenant_id: str, job_id: str, new_ver: int) -> None:
    """递增执行版本并重新入队调度任务（仅启用 Kafka 时写入 Outbox）。

    Args:
        conn: 事务中的数据库连接。
        tenant_id: 所属租户标识。
        job_id: 任务主键编号。
        new_ver: 递增后的执行版本号。
    """
    conn.execute(
        "UPDATE jobs SET state='queued',stage='工作流重新排队调度中...',progress=0,error='',cancel_requested=false,"
        "run_version=%s,started_at=NULL,finished_at=NULL,lease_until=NULL WHERE id=%s",
        (new_ver, job_id),
    )
    conn.execute(
        "INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,'工作流重新排队调度中...',0,'queued')",
        (tenant_id, job_id, new_ver),
    )
    if load_config().kafka_enabled:
        conn.execute("INSERT INTO task_outbox(job_id) VALUES(%s)", (job_id,))


def save_task_review(conn: Any, tenant_id: str, job_id: str, reviewer_id: str, approve: bool, comment: str) -> None:
    """写入业务答案人工审核结论与任务终态。"""
    conn.execute(
        "UPDATE jobs SET state=%s,finished_at=now(),progress=100,stage=%s WHERE id=%s",
        ("completed" if approve else "rejected", "已发布" if approve else "审核未通过", job_id),
    )
    conn.execute(
        "INSERT INTO task_reviews(tenant_id,job_id,reviewer_id,decision,comment) VALUES(%s,%s,%s,%s,%s)",
        (tenant_id, job_id, reviewer_id, "approved" if approve else "rejected", comment),
    )


def present_job(
    conn: Any,
    p: Any,
    job: dict,
    full: bool = True,
    *,
    valid_override: bool | None = None,
) -> dict:
    """按当前用户权限与来源实时有效状态构造任务展示视图。

    Args:
        conn: 数据库连接。
        p: 当前请求主体。
        job: 原始任务行字典。
        full: 是否展开 result 与完整事件时间线。
        valid_override: 可选批量预校验的来源有效性布尔值（避免列表场景重复查询）。

    Returns:
        已完成字段脱敏与越权过滤的展示字典。
    """
    if not can_task(p, job):
        raise Denied("任务不存在或没有访问权限", "NOT_FOUND", 404)
    source_ok = valid_override if valid_override is not None else sources_valid(conn, p, job["sources"])
    valid = job["state"] != "legacy" and source_ok
    result = job["result"] if valid and (job["state"] == "completed" or (job["state"] == "review" and can_review(p, job))) else None
    is_privileged_viewer = p.boss or p.id == job["owner_id"]
    events = []
    if full:
        rows = list_task_events(conn, p.tenant_id, job["id"])
        events = [
            dict(
                id=r["id"],
                stage=r["stage"] if is_privileged_viewer else redact(str(r["stage"])),
                progress=r["progress"],
                status=r["status"],
                created_at=r["created_at"].isoformat() if r.get("created_at") else None,
            )
            for r in rows
        ]
    return dict(
        id=job["id"],
        question=(job["question"] if is_privileged_viewer else redact(job["question"])) if valid else "来源权限已变化，请重新查询",
        kind=job["kind"],
        state=job["state"],
        stage=job["stage"],
        progress=job["progress"],
        created_at=job["created_at"],
        finished_at=job["finished_at"],
        error=job["error"],
        input_level=job["input_level"],
        node_id=job["node_id"],
        owner_id=job["owner_id"],
        result=result if full else None,
        events=events,
        restricted=not valid,
        can_review=can_review(p, job) and valid,
        can_cancel=p.id == job["owner_id"] or p.boss,
    )
