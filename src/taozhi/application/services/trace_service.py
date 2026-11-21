"""按当前成员或管理员选择的发起人查看提问及执行轨迹。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from taozhi.domain.access import RESTRICTED_QUESTION
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.domain.trace_activity import CHAIN_CATEGORY_BY_NAME, CHAIN_OPERATIONS, activity_category
from taozhi.infrastructure.persistence import trace_repo
from taozhi.infrastructure.persistence.access_repo import batch_sources_validity, sources_valid
from taozhi.knowledge.db import database_connection
from taozhi.observability.diagnostics import safe_diagnostic
from taozhi.observability.span_attributes import safe_attributes

_SPAN_FLUSH_GRACE = timedelta(seconds=10)
_TRACE_OPERATIONS = {
    "task.ask": "问答任务",
    "task.research": "选品调研任务",
    "worker.question": "知识问答流程",
    "worker.bi": "经营问数流程",
    "worker.research": "选品调研流程",
    "worker.semantic_cache_hit": "复用已核验答案",
    "pipeline.planning": "识别问题与分派角色",
    "pipeline.retrieval": "资料检索流程",
    "knowledge.search": "检索授权资料",
    "knowledge.cache_lookup": "查询问答缓存",
    "pipeline.specialists": "专业核验",
    "pipeline.publication": "生成答复",
    "bi.bi_intent": "识别经营指标",
    "bi.data_query": "查询经营数据",
    "bi.query_metric": "检索经营指标",
    "bi.bi_publish": "生成指标答复",
    "research.final_node": "归档调研成果",
    "llm.structured_output": "获取并校验模型结构化结果",
    "embedding.query": "生成查询向量",
    "embedding.passages": "生成文档向量",
    "reranker.rerank": "重排检索结果",
    **CHAIN_OPERATIONS["CONTEXT"],
    **CHAIN_OPERATIONS["GUARDRAIL"],
}
_KEY_ACTIVITY_STEPS = (
    "worker.semantic_cache_hit", "pipeline.planning", "pipeline.publication",
    "bi.bi_intent", "bi.bi_publish", "research.final_node", *CHAIN_CATEGORY_BY_NAME,
)
_TOOL_OUTCOMES = {
    "timeout": "工具调用超时",
    "empty": "未找到匹配结果",
    "cancelled": "工具调用已取消",
    "error": "工具调用失败",
}
_KNOWN_FAILURE_STAGES = {
    "任务重试次数已达上限",
    "任务处理超时，请重试",
    "任务执行版本已失效",
    "任务已取消或执行版本已失效",
    "任务已取消或执行租约失效",
    "人员权限已变化，请重新提交",
    "人员或来源权限已变化",
    "资料来源或访问权限已变化",
    "当前问题不允许发送外部模型",
}


def _visible_owner(p: Principal, requested: str | None = None) -> str | None:
    """从会话身份确定可查发起人，管理员可以选择本租户全部账号。"""
    if p.system_admin:
        return None if requested in (None, "all") else requested
    if requested is not None and requested != p.id:
        raise Denied("只能查看本人发起的轨迹")
    return p.id


def list_owners(p: Principal, *, query: str, cursor: str | None, limit: int) -> dict:
    """为管理员的发起人选择框提供分页账号目录。"""
    if not p.system_admin:
        raise Denied("选择其他发起人需要系统管理员身份")
    with database_connection() as conn:
        rows = trace_repo.list_owners(conn, p.tenant_id, query=query.strip(), after=cursor, limit=limit + 1)
    return {"items": rows[:limit], "next_cursor": rows[limit - 1]["username"] if len(rows) > limit else None}


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """将分页边界还原为带时区的时间戳与唯一键。"""
    if cursor is None:
        return None
    timestamp, _, task_id = cursor.partition("|")
    try:
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", task_id):
            raise ValueError
    except (ValueError, OverflowError) as err:
        raise Denied("轨迹分页游标无效", "INVALID_CURSOR", 422) from err
    return created_at, task_id


def _display_stage(stage: str, status: str) -> str:
    """隐藏旧事件中内联的工具报错、模型目标及干预正文，保留执行阶段。"""
    if status == "failed":
        return stage if stage in _KNOWN_FAILURE_STAGES else "任务处理失败"
    if status == "cancelled":
        if stage in {"审核等待超时，任务已自动关闭", "审核已超时关闭"}:
            return stage
        return "任务已取消"
    if stage.startswith("【用户中途追加干预指令】"):
        return "已收到用户中途追加的干预指令"
    if stage.startswith("[system] [模型容灾]"):
        return "[system] 模型通道切换"
    if stage.startswith("[agent_start]"):
        stage = stage.partition("· 目标：")[0].partition("· 调研主题：")[0].rstrip()
    elif stage.startswith("[tool_execution_end]") and "· 状态=" in stage:
        label, _, diagnostic = stage.partition("· 状态=")
        outcome = diagnostic.partition(":")[0].strip()
        stage = f"{label.rstrip()}（{_TOOL_OUTCOMES.get(outcome, '未取得结果')}）"
    return redact(stage[:300])


def _display_trace(row: dict, question_visible: bool) -> dict:
    """按工作台脱敏规则展示原题和当前阶段。"""
    trace = {key: value for key, value in row.items() if key not in {"sources", "matches_metadata"}}
    trace["question"] = redact(row["question"]) if question_visible else RESTRICTED_QUESTION
    trace["stage"] = _display_stage(row["stage"], row["state"])
    return trace


def list_traces(
    p: Principal, *, kind: str | None, state: str | None, query: str, cursor: str | None, limit: int,
    owner_id: str | None = None,
) -> dict:
    """按可见发起人、检索条件及稳定游标读取提问。"""
    visible_owner = _visible_owner(p, owner_id)
    before = _decode_cursor(cursor)
    query = query.strip()
    batch_size = max(limit + 1, 50) if query and not p.system_admin else limit + 1
    rows = []
    with database_connection() as conn:
        while len(rows) <= limit:
            batch = trace_repo.list_traces(
                conn, p.tenant_id, owner_id=visible_owner, kind=kind, state=state, query=query,
                before=before, limit=batch_size, include_sources=not p.system_admin,
            )
            validity = {} if p.system_admin else batch_sources_validity(
                conn, p, [row for row in batch if row["state"] != "legacy"]
            )
            for row in batch:
                question_visible = p.system_admin or (row["state"] != "legacy" and validity[row["id"]])
                if question_visible or row["matches_metadata"]:
                    rows.append(_display_trace(row, question_visible))
            if len(batch) < batch_size:
                break
            before = batch[-1]["created_at"], batch[-1]["id"]
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = f"{last['created_at'].isoformat()}|{last['id']}"
    return {"items": page, "next_cursor": next_cursor}


def get_trace(
    p: Principal, task_id: str, *, after_event_id: int, event_limit: int,
) -> dict:
    """按任务读取状态事件，支持运行中增量拉取。"""
    with database_connection() as conn:
        row = trace_repo.get_trace(conn, p.tenant_id, task_id, owner_id=_visible_owner(p))
        if row is None:
            raise Denied("轨迹不存在", "NOT_FOUND", 404)
        question_visible = p.system_admin or (row["state"] != "legacy" and sources_valid(conn, p, row["sources"]))
        events = trace_repo.list_events(conn, p.tenant_id, task_id, after_event_id, event_limit + 1)
    return {
        "trace": _display_trace(row, question_visible),
        "events": [
            {**event, "stage": _display_stage(event["stage"], event["status"])}
            for event in events[:event_limit]
        ],
        "has_more_events": len(events) > event_limit,
    }


def _span_status(recorded: str | None, job: dict, run_version: int) -> str:
    """已失去执行权的未关闭节点显示为中断；保留成功恢复前各子节点的真实错误。"""
    if recorded and recorded != "running":
        return recorded
    if job["run_version"] != run_version:
        return "interrupted"
    now = job["observed_at"]
    if job["state"] == "running":
        return "running" if job["lease_until"] and job["lease_until"] > now else "interrupted"
    if job["finished_at"] and now - job["finished_at"] < _SPAN_FLUSH_GRACE:
        return "running"
    return "interrupted"


def list_runs(p: Principal, task_id: str, *, cursor: str | None, trace_id: str | None, limit: int) -> dict:
    """列出每次执行的实际链路，使用执行版本与链路编号共同定位翻页边界。"""
    before = None
    if cursor is not None:
        match = re.fullmatch(r"([0-9]{1,19})[|]([0-9a-f]{32})", cursor)
        if not match or int(match[1]) > 2**63 - 1:
            raise Denied("链路分页游标无效", "INVALID_CURSOR", 422)
        before = int(match[1]), match[2]
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, owner_id=_visible_owner(p))
        if job is None:
            raise Denied("轨迹不存在", "NOT_FOUND", 404)
        rows = trace_repo.list_span_runs(conn, p.tenant_id, task_id, before=before, trace_id=trace_id, limit=limit + 1)
    page = [
        {**row, "status": _span_status(row["status"], job, row["run_version"])}
        for row in rows[:limit]
    ]
    next_cursor = f"{page[-1]['run_version']}|{page[-1]['trace_id']}" if len(rows) > limit else None
    return {"items": page, "next_cursor": next_cursor}


def _span_display_name(kind: str, operation: str, attributes: dict) -> str:
    """用本次调用实际记录的模型、工具或业务角色标识命名节点。"""
    if kind == "LLM":
        return attributes.get("llm.model_name", "模型推理")
    if kind == "TOOL":
        return attributes.get("tool.name", operation)
    if kind == "AGENT":
        return agent_display_name(attributes.get("agent.name", operation))
    return _TRACE_OPERATIONS.get(operation, operation)


def _display_span(row: dict, job: dict) -> dict:
    """为节点详情和活动记录提供一致的状态、名称与可展示属性。"""
    attributes = safe_attributes(row["attributes"])
    fields = ("trace_id", "span_id", "parent_span_id", "name", "kind", "started_at", "ended_at", "duration_ms", "error_type")
    return {
        **{key: row[key] for key in fields},
        "status": _span_status(row["status"], job, job["span_version"]),
        "category": activity_category(row["kind"], row["name"]),
        "attributes": attributes,
        "diagnostics": [safe for item in row["diagnostics"]
                        if isinstance(item, dict) and (safe := safe_diagnostic(item, item.get("timestamp"))) is not None],
        "display_name": _span_display_name(row["kind"], row["name"], attributes),
    }


def list_spans(p: Principal, task_id: str, trace_id: str, *, kind: str | None, after_id: int, limit: int) -> dict:
    """在任务归属范围内，按精确链路编号和节点类型读取采集记录。"""
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, owner_id=_visible_owner(p), trace_id=trace_id)
        if job is None:
            raise Denied("执行轨迹不存在", "NOT_FOUND", 404)
        rows = trace_repo.list_spans(conn, p.tenant_id, task_id, trace_id, kind=kind, after_id=after_id, limit=limit + 1)
    next_cursor = str(rows[limit - 1]["id"]) if len(rows) > limit else None
    return {
        "items": [_display_span(row, job) for row in rows[:limit]],
        "next_cursor": next_cursor,
        "observed_at": job["observed_at"],
    }


def _activity_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    """校验活动游标中的数据库主键，拒绝超出 bigint 范围的边界。"""
    boundary = _decode_cursor(cursor)
    if boundary is None:
        return None
    started_at, row_id = boundary
    if not re.fullmatch(r"[1-9][0-9]{0,18}", row_id) or int(row_id) > 2**63 - 1:
        raise Denied("活动分页游标无效", "INVALID_CURSOR", 422)
    return started_at, int(row_id)


def _activity_actor(row: dict) -> dict | None:
    """使用已验证的祖先节点命名执行者，缺少父链时保留为空。"""
    if row["actor_span_id"] is None:
        return None
    kind = row["actor_kind"]
    attributes = safe_attributes({"agent.name": row["actor_role"]})
    return {
        "kind": "agent" if kind == "AGENT" else "workflow",
        "span_id": row["actor_span_id"],
        "display_name": _span_display_name(kind, row["actor_operation"], attributes),
    }


def list_activities(
    p: Principal, task_id: str, trace_id: str, *, category: str | None, diagnostic: str | None = None, cursor: str | None, limit: int,
) -> dict:
    """按精确的链路与执行版本展示实际操作，并在筛选后保留跨页执行者。"""
    after = _activity_cursor(cursor)
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, owner_id=_visible_owner(p), trace_id=trace_id)
        if job is None:
            raise Denied("执行轨迹不存在", "NOT_FOUND", 404)
        rows = trace_repo.list_activities(
            conn, p.tenant_id, task_id, trace_id, job["span_version"], category=category, diagnostic=diagnostic,
            key_steps=_KEY_ACTIVITY_STEPS, after=after, limit=limit + 1,
        )
    page = rows[:limit]
    next_cursor = f"{page[-1]['started_at'].isoformat()}|{page[-1]['id']}" if len(rows) > limit else None
    return {
        "items": [{**_display_span(row, job), "actor": _activity_actor(row)} for row in page],
        "next_cursor": next_cursor,
        "observed_at": job["observed_at"],
    }
