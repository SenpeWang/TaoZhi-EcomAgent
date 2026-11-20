"""按当前成员或管理员选择的发起人查看提问及执行轨迹。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from taozhi.domain.access import RESTRICTED_QUESTION
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence import trace_repo
from taozhi.infrastructure.persistence.access_repo import batch_sources_validity, sources_valid
from taozhi.knowledge.db import database_connection
from taozhi.observability.span_attributes import safe_attributes

_MODEL_CALL_PAGE_SIZE = 100
_SPAN_FLUSH_GRACE = timedelta(seconds=10)
_TRACE_OPERATIONS = {
    "task.ask": "问答任务",
    "task.research": "选品调研任务",
    "worker.question": "知识问答流程",
    "worker.bi": "经营问数流程",
    "worker.research": "选品调研流程",
    "worker.semantic_cache_hit": "复用已核验答案",
    "pipeline.planning": "识别问题与分派角色",
    "pipeline.retrieval": "检索授权资料",
    "pipeline.specialists": "专业核验",
    "pipeline.quality": "校验事实与引用",
    "pipeline.publication": "生成答复",
    "bi.bi_intent": "识别经营指标",
    "bi.data_query": "查询经营数据",
    "bi.bi_publish": "生成指标答复",
    "research.final_node": "归档调研成果",
    "llm.chat": "模型推理",
    "mcp.tool.search_web": "检索市场信息",
    "mcp.tool.fetch_web_page": "读取网页正文",
    "mcp.tool.search_1688_wholesale": "查询 1688 批发报价",
    "mcp.tool.search_amazon": "查询亚马逊竞品",
}
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
    """将分页边界还原为带时区的时间戳和任务编号。"""
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
    # 个人搜索中，一批原题匹配结果可能因文档撤权而全部不可见。
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
    p: Principal, task_id: str, *, after_event_id: int, event_limit: int, before_call_id: int | None = None,
) -> dict:
    """按任务读取真实事件与模型调用账本，支持运行中增量拉取。"""
    with database_connection() as conn:
        row = trace_repo.get_trace(conn, p.tenant_id, task_id, owner_id=_visible_owner(p))
        if row is None:
            raise Denied("轨迹不存在", "NOT_FOUND", 404)
        question_visible = p.system_admin or (row["state"] != "legacy" and sources_valid(conn, p, row["sources"]))
        events = trace_repo.list_events(conn, p.tenant_id, task_id, after_event_id, event_limit + 1)
        calls = trace_repo.list_model_calls(conn, p.tenant_id, task_id, before_call_id, _MODEL_CALL_PAGE_SIZE + 1)
    trace_id = row.pop("trace_id")
    return {
        "trace": _display_trace(row, question_visible),
        "events": [
            {**event, "stage": _display_stage(event["stage"], event["status"])}
            for event in events[:event_limit]
        ],
        "has_more_events": len(events) > event_limit,
        "model_calls": [
            {**call, "role": redact(call["role"]), "role_label": redact(agent_display_name(call["role"]))}
            for call in reversed(calls[:_MODEL_CALL_PAGE_SIZE])
        ],
        "has_more_calls": len(calls) > _MODEL_CALL_PAGE_SIZE,
        "trace_id": trace_id if trace_id and re.fullmatch(r"[0-9a-f]{32}", trace_id) else None,
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
    # 任务提交终态后，后台 Span 写线程仍可在短时间内提交本次执行的结束快照。
    if job["finished_at"] and now - job["finished_at"] < _SPAN_FLUSH_GRACE:
        return "running"
    return "interrupted"


def list_runs(p: Principal, task_id: str, *, before_version: int | None, limit: int) -> dict:
    """列出提问的实际采集记录，旧任务与尚未开始执行的任务均可返回空页。"""
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, owner_id=_visible_owner(p))
        if job is None:
            raise Denied("轨迹不存在", "NOT_FOUND", 404)
        rows = trace_repo.list_span_runs(conn, p.tenant_id, task_id, before_version, limit + 1)
    page = [
        {**row, "status": _span_status(row["status"], job, row["run_version"])}
        for row in rows[:limit]
    ]
    return {"items": page, "next_before_version": page[-1]["run_version"] if len(rows) > limit else None}


def list_spans(p: Principal, task_id: str, run_version: int, *, after_id: int, limit: int) -> dict:
    """读取一个执行版本的真实节点，只展示允许进入管理界面的元数据。"""
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, owner_id=_visible_owner(p), run_version=run_version)
        if job is None:
            raise Denied("执行轨迹不存在", "NOT_FOUND", 404)
        rows = trace_repo.list_spans(conn, p.tenant_id, task_id, run_version, after_id, limit + 1)
    next_cursor = str(rows[limit - 1]["id"]) if len(rows) > limit else None
    page = rows[:limit]
    for row in page:
        del row["id"]
        row["status"] = _span_status(row["status"], job, run_version)
        row["attributes"] = safe_attributes(row["attributes"])
        operation = row["name"]
        role_id = operation.removeprefix("rag.specialist.")
        if operation == "research.subagent_worker":
            role_id = str(row["attributes"].get("gen_ai.agent.name", operation))
        row["display_name"] = _TRACE_OPERATIONS.get(operation) or agent_display_name(role_id)
    return {"items": page, "next_cursor": next_cursor, "observed_at": job["observed_at"]}
