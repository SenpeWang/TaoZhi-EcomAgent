"""采集调研执行结果，并将合成记录还原为可独立评分的观测。"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from taozhi.domain.research import SOURCE_TOOLS


def _unknown_usage() -> dict:
    return {"model_calls": None, "measured_calls": 0, "prompt_tokens": None, "completion_tokens": None}


@dataclass
class AgentRunResult:
    """一次真实运行或记录重评分的观测。缺失的计量和证据保持未知。"""

    report: str
    system_status: str
    tool_calls: list[dict] = field(default_factory=list)
    subagents_count: int | None = None
    elapsed: float | None = None
    trace_id: str = ""
    steering_applied: bool = False
    error: str | None = None
    evidence_ids: list[str] | None = None
    token_usage: dict = field(default_factory=_unknown_usage)
    stop_reason: str | None = None
    tool_trace_complete: bool = True
    model_names: list[str] = field(default_factory=list)
    steering_after_sequence: int | None = None
    subagent_roles: list[str] = field(default_factory=list)
    evidence_roles: list[str] = field(default_factory=list)


def _run_research(*, observe_trace: Callable[[str], None], **kwargs: Any) -> dict:
    """按需加载在线依赖，将本次执行关联到独立的评测轨迹。"""
    from taozhi.application.research import graph
    from taozhi.observability.tracing import current_trace_id, node_span

    with node_span("eval.agent_run", "CHAIN"):
        observe_trace(current_trace_id())
        return graph.execute_autonomous_agents(**kwargs)


class _RunObservation:
    """收集并行节点的用量，在首个调研来源工具完成后交付干预。"""

    def __init__(self, steering: list[str]) -> None:
        self._lock = threading.Lock()
        self.steering = steering
        self.tool_finished = False
        self.delivered = False
        self.received = False
        self.trace_id = ""
        self.model_attempts = 0
        self.model_names: set[str] = set()
        self.measured = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def observe_trace(self, trace_id: str) -> None:
        self.trace_id = trace_id

    def observe_progress(self, stage: str, progress: int) -> None:
        with self._lock:
            completion = re.match(r"^\[tool_execution_end\] \[[^\]]+\] (\w+)\b", stage)
            if completion and completion[1] in SOURCE_TOOLS:
                self.tool_finished = True
            if stage.startswith("[steering]") and self.delivered:
                self.received = True

    def fetch_steering(self) -> list[str]:
        with self._lock:
            if self.tool_finished and self.steering and not self.delivered:
                self.delivered = True
                return list(self.steering)
            return []

    def observe_model(self, role: str, event: dict) -> None:
        """上游把缺失的 Token 计为零，仅在两个计数均可确认为实测时纳入小计。"""
        with self._lock:
            self.model_attempts += 1
            if isinstance(event.get("model"), str) and event["model"].strip():
                self.model_names.add(event["model"].strip())
            prompt, completion = event.get("prompt_tokens"), event.get("completion_tokens")
            if type(prompt) is int and type(completion) is int and prompt > 0 and completion > 0:
                self.measured += 1
                self.prompt_tokens += prompt
                self.completion_tokens += completion

    def usage(self, reported_calls: int | None = None) -> dict:
        with self._lock:
            count = self.model_attempts or None
            if type(reported_calls) is int and reported_calls >= 0:
                count = max(reported_calls, self.model_attempts)
            return {
                "model_calls": count,
                "measured_calls": self.measured,
                "prompt_tokens": self.prompt_tokens if self.measured else None,
                "completion_tokens": self.completion_tokens if self.measured else None,
            }


def _tool_observations(subagents: list[dict]) -> list[dict]:
    """将已送入 MCP 客户端的工具记录与派发前的授权拒绝分开。"""
    calls = []
    workers = [agent for agent in subagents if isinstance(agent, dict) and isinstance(agent.get("tools"), list) and agent["tools"]]
    for worker in workers:
        for position, tool in enumerate(worker["tools"], start=1):
            calls.append({
                "tool_name": tool["name"],
                "parameters": tool.get("parameters"),
                "status": tool.get("status"),
                "data_count": tool.get("data_count"),
                "dispatched": tool.get("dispatched"),
                "sequence": position if len(workers) == 1 else None,
            })
    return calls


def _evidence_ids(evidence_pool: list[dict] | None) -> list[str] | None:
    """只取实际返回的有效证据与来源。空清单表示已观测但没有来源。"""
    if evidence_pool is None:
        return None
    ids = []
    for evidence in evidence_pool:
        if evidence.get("status") != "ok":
            continue
        ids.append(evidence.get("evidence_id"))
        for items in evidence.get("platform_items", {}).values():
            for item in items:
                ids.extend((item.get("evidence_id"), item.get("url")))
    return list(dict.fromkeys(value for value in ids if isinstance(value, str) and value))


class AgentEvalHarness:
    """从真实调研入口执行评测，测试可注入相同调用契约的执行函数。"""

    def __init__(self, *, execute: Callable[..., dict] = _run_research) -> None:
        self.execute = execute

    def run(self, case: dict) -> AgentRunResult:
        if case.get("execution_mode") == "synthetic_trace_regrade":
            raise ValueError("此用例需要合成执行记录，请使用回放入口")
        prompt = case["prompt"]
        keyword = case.get("keyword") or prompt
        observer = _RunObservation(case.get("steering", []))
        started = time.perf_counter()
        result = AgentRunResult(report="", system_status="UNKNOWN")
        try:
            raw = self.execute(
                keyword=keyword, user_query=prompt, job_id=f"eval_{uuid4().hex}", tenant_id="eval_tenant",
                observe_trace=observer.observe_trace, model_call_fn=observer.observe_model,
                emit_event_fn=observer.observe_progress, fetch_steering_fn=observer.fetch_steering,
            )
            result.report = raw.get("report", "")
            subagents = raw.get("subagents")
            result.tool_trace_complete = isinstance(subagents, list) and all(
                isinstance(agent, dict) and isinstance(agent.get("tools"), list) for agent in subagents
            )
            if isinstance(subagents, list):
                result.subagents_count = len(subagents)
                result.tool_calls = _tool_observations(subagents)
                result.subagent_roles = sorted(
                    {str(agent.get("role")) for agent in subagents if isinstance(agent, dict) and agent.get("role")}
                )
            evidence_pool = raw.get("evidence_pool")
            result.evidence_ids = _evidence_ids(evidence_pool)
            if isinstance(evidence_pool, list):
                result.evidence_roles = sorted(
                    {str(item.get("role")) for item in evidence_pool if isinstance(item, dict) and item.get("role")}
                )
            result.system_status = raw.get("system_status") or "UNKNOWN"
            result.stop_reason = raw.get("stop_reason")
            result.token_usage = observer.usage(raw.get("model_calls"))
        except Exception as exc:
            result.error = type(exc).__name__
            result.system_status = "FAILED"
            result.tool_trace_complete = False
            result.token_usage = observer.usage()
        result.trace_id = observer.trace_id
        result.elapsed = time.perf_counter() - started
        result.steering_applied = observer.received
        result.model_names = sorted(observer.model_names)
        return result


def _nonnegative_integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _transcript_usage(raw: dict | None) -> dict:
    if raw is None:
        return _unknown_usage()
    if not isinstance(raw, dict):
        raise ValueError("token_usage 必须是对象")
    if raw.keys() - _unknown_usage().keys():
        raise ValueError("token_usage 包含未知计量字段")
    usage = {**_unknown_usage(), **raw}
    calls, measured = usage["model_calls"], usage["measured_calls"]
    if calls is not None and not _nonnegative_integer(calls):
        raise ValueError("model_calls 必须是非负整数")
    if not _nonnegative_integer(measured) or (calls is not None and measured > calls):
        raise ValueError("measured_calls 超出可用调用数")
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage[key]
        if measured and not _nonnegative_integer(value):
            raise ValueError("有计量的调用必须提供输入和输出 Token 数")
        if not measured and value is not None:
            raise ValueError("没有计量的调用不能提供 Token 小计")
    return usage


def replay_transcript(case: dict, transcript: dict) -> AgentRunResult:
    """校验并重评分合成执行记录，整个入口独立于模型、工具、数据库和追踪 SDK。"""
    if not isinstance(transcript, dict) or transcript.get("case_id") != case["id"]:
        raise ValueError("记录中的 case_id 必须匹配待评分用例")
    if "fixture_version" in case and (
        type(transcript.get("fixture_version")) is not int or transcript["fixture_version"] != case["fixture_version"]
    ):
        raise ValueError("记录与用例的合成证据版本不匹配")
    status = transcript.get("system_status")
    report = transcript.get("report")
    if not isinstance(status, str) or not status.strip() or not isinstance(report, str):
        raise ValueError("合成记录必须提供 system_status 与 report")
    calls = transcript.get("tool_calls")
    if not isinstance(calls, list):
        raise ValueError("tool_calls 必须是数组")
    sequences = set()
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("tool_name"), str) or not call["tool_name"].strip():
            raise ValueError("调用记录必须提供工具名")
        if call.get("parameters") is not None and not isinstance(call["parameters"], dict):
            raise ValueError("parameters 必须是对象")
        if call.get("status") is not None and call["status"] not in ("ok", "empty", "error", "timeout", "cancelled"):
            raise ValueError("调用记录包含未知工具状态")
        if call.get("dispatched") is not None and type(call["dispatched"]) is not bool:
            raise ValueError("dispatched 必须是布尔值")
        if call.get("data_count") is not None and not _nonnegative_integer(call["data_count"]):
            raise ValueError("data_count 必须是非负整数")
        sequence = call.get("sequence")
        if sequence is not None:
            if not _nonnegative_integer(sequence) or sequence in sequences:
                raise ValueError("sequence 必须是唯一的非负整数")
            sequences.add(sequence)
    ids = transcript.get("evidence_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids)):
        raise ValueError("evidence_ids 必须是证据标识数组")
    elapsed = transcript.get("elapsed")
    if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("elapsed 必须是有限的非负数")
    steering = transcript.get("steering_applied", False)
    complete = transcript.get("tool_trace_complete", True)
    if type(steering) is not bool or type(complete) is not bool:
        raise ValueError("steering_applied 与 tool_trace_complete 必须是布尔值")
    boundary = transcript.get("steering_after_sequence")
    if boundary is not None and (
        not steering or not _nonnegative_integer(boundary) or (boundary != 0 and boundary not in sequences)
    ):
        raise ValueError("干预边界须关联到指令到达前的最后一次调用序号，启动前指令使用 0")
    trace_id = transcript.get("trace_id", "")
    subagent_roles = transcript.get("subagent_roles", [])
    evidence_roles = transcript.get("evidence_roles", [])
    for label, values in (("subagent_roles", subagent_roles), ("evidence_roles", evidence_roles)):
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{label} 必须是字符串数组")
    model_names = transcript.get("model_names", [])
    if not isinstance(model_names, list) or not all(isinstance(name, str) and name.strip() for name in model_names):
        raise ValueError("model_names 必须是已观察到的模型名称列表")
    error, stop_reason = transcript.get("error"), transcript.get("stop_reason")
    if not isinstance(trace_id, str) or any(value is not None and not isinstance(value, str) for value in (error, stop_reason)):
        raise ValueError("trace_id、stop_reason 和 error 必须是字符串")
    return AgentRunResult(
        report=report, tool_calls=calls, elapsed=elapsed,
        trace_id=trace_id, system_status=status, steering_applied=steering, error=error,
        evidence_ids=ids, token_usage=_transcript_usage(transcript.get("token_usage")),
        stop_reason=stop_reason, tool_trace_complete=complete, model_names=sorted(set(model_names)),
        steering_after_sequence=boundary, subagent_roles=list(subagent_roles), evidence_roles=list(evidence_roles),
    )
