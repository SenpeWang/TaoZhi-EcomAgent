"""Agent 评测会话沙箱与上下文追踪适配器（移植自 Piagent harness.ts 规范）。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from taozhi.observability.tracing import current_trace_id, node_span
from taozhi.orchestration.agent_system import execute_autonomous_agents


@dataclass
class AgentRunResult:
    """单个 Agent 任务评测运行归一化结果。"""
    case_id: str
    task_prompt: str
    keyword: str
    report: str
    subagents: List[Dict[str, Any]]
    tool_calls: List[Dict[str, Any]]
    state_transitions: List[Dict[str, Any]]
    artifacts: List[str]
    elapsed: float
    trace_id: str
    system_status: str
    steering_applied: bool = False
    error: Optional[str] = None


class AgentEvalHarness:
    """负责评测用例的隔离运行、Arize Phoenix Trace 注入与诊断信息收集。"""

    def __init__(self, checkpointer: Optional[Any] = None) -> None:
        self.checkpointer = checkpointer

    def run(self, case: Dict[str, Any], initial_steering: Optional[List[str]] = None) -> AgentRunResult:
        case_id = case["id"]
        task_prompt = case["prompt"]
        keyword = case.get("keyword") or task_prompt
        steering = initial_steering or case.get("steering") or []
        t0 = time.perf_counter()

        with node_span("eval.agent_run", {"case_id": case_id, "keyword": keyword}):
            trace_id = current_trace_id()
            try:
                raw_res = execute_autonomous_agents(
                    keyword=keyword,
                    user_query=task_prompt,
                    job_id=f"eval_{case_id.lower().replace('-', '_')}",
                    tenant_id="eval_tenant",
                    checkpointer=self.checkpointer,
                    initial_steering=list(steering) if steering else None,
                )
                elapsed = round(time.perf_counter() - t0, 2)

                # 提取扁平化的工具调用明细
                tool_calls = []
                for sub in raw_res.get("subagents", []):
                    for t in sub.get("tools", []):
                        tool_calls.append({
                            "agent_role": sub.get("role"),
                            "agent_type": sub.get("type_name"),
                            "tool_name": t.get("name"),
                            "parameters": t.get("parameters", {}),
                            "result_summary": t.get("description", ""),
                            "data_count": t.get("data_count", 0),
                        })

                return AgentRunResult(
                    case_id=case_id,
                    task_prompt=task_prompt,
                    keyword=keyword,
                    report=raw_res.get("report", ""),
                    subagents=raw_res.get("subagents", []),
                    tool_calls=tool_calls,
                    state_transitions=raw_res.get("audit_trail", []),
                    artifacts=raw_res.get("artifacts", []),
                    elapsed=elapsed,
                    trace_id=trace_id,
                    system_status=raw_res.get("system_status", "COMPLETED"),
                    steering_applied=bool(steering),
                )
            except Exception as exc:
                elapsed = round(time.perf_counter() - t0, 2)
                return AgentRunResult(
                    case_id=case_id,
                    task_prompt=task_prompt,
                    keyword=keyword,
                    report="",
                    subagents=[],
                    tool_calls=[],
                    state_transitions=[],
                    artifacts=[],
                    elapsed=elapsed,
                    trace_id=trace_id,
                    system_status="FAILED",
                    error=f"{type(exc).__name__}: {str(exc)}",
                )
