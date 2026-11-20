"""系统管理员查看本租户所有提问及执行轨迹的只读用例。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.entities import Principal
from taozhi.domain.errors import Denied
from taozhi.domain.privacy import redact
from taozhi.infrastructure.persistence import trace_repo
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


def _require_trace_admin(p: Principal) -> None:
    """跨用户读取提问需要系统管理员身份，业务管理职务不授予该权限。"""
    if not p.system_admin:
        raise Denied("查看轨迹中台需要系统管理员身份")


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


def _display_trace(row: dict) -> dict:
    """按工作台脱敏规则展示原题和当前阶段。"""
    return {**row, "question": redact(row["question"]), "stage": _display_stage(row["stage"], row["state"])}


def list_traces(
    p: Principal, *, kind: str | None, state: str | None, query: str, cursor: str | None, limit: int,
) -> dict:
    """按系统管理员的租户、检索条件及稳定游标读取跨用户提问。"""
    _require_trace_admin(p)
    before = _decode_cursor(cursor)
    with database_connection() as conn:
        rows = trace_repo.list_traces(
            conn, p.tenant_id, kind=kind, state=state, query=query.strip(), before=before, limit=limit + 1
        )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = f"{last['created_at'].isoformat()}|{last['id']}"
    return {"items": [_display_trace(row) for row in page], "next_cursor": next_cursor}


def get_trace(
    p: Principal, task_id: str, *, after_event_id: int, event_limit: int, before_call_id: int | None = None,
) -> dict:
    """按任务读取真实事件与模型调用账本，支持运行中增量拉取。"""
    _require_trace_admin(p)
    with database_connection() as conn:
        row = trace_repo.get_trace(conn, p.tenant_id, task_id)
        if row is None:
            raise Denied("轨迹不存在", "NOT_FOUND", 404)
        events = trace_repo.list_events(conn, p.tenant_id, task_id, after_event_id, event_limit + 1)
        calls = trace_repo.list_model_calls(conn, p.tenant_id, task_id, before_call_id, _MODEL_CALL_PAGE_SIZE + 1)
    trace_id = row.pop("trace_id")
    return {
        "trace": _display_trace(row),
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
    _require_trace_admin(p)
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id)
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
    _require_trace_admin(p)
    with database_connection() as conn:
        job = trace_repo.get_span_job(conn, p.tenant_id, task_id, run_version)
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
