"""MCP 智能体客户端适配器（McpClientAdapter）。

将标准 MCP 协议（tools/list 与 tools/call）封装为受控的多智能体工具客户端：
1. 动态从 EcommerceMcpServer 发现合法工具元数据与 JSON Schema；
2. 统一复用核心执行器 ``execute_tool_with_outcome`` 管控预算、取消信号、线程池与 ToolOutcome 转换。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Sequence

from openinference.semconv.trace import SpanAttributes
from opentelemetry.trace import StatusCode

from taozhi.core.tools.outcome import (
    ToolOutcome,
    clamp_timeout_args,
    execute_tool_with_outcome,
)
from taozhi.observability.diagnostics import EVENT_NAME
from taozhi.observability.tracing import node_span, record_diagnostic
from .server import EcommerceMcpServer, default_mcp_server


class _McpToolCallError(RuntimeError):
    """MCP 服务端返回 isError=True 时的结构化异常传递载体。"""


class McpClientAdapter:
    """面向多智能体系统的标准 MCP 协议工具客户端。"""

    def __init__(self, server: Optional[EcommerceMcpServer] = None) -> None:
        self.server = server or default_mcp_server

    def list_tool_specs(self, allowed_tools: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """通过 MCP tools/list 获取受允许工具的元数据与 Schema。

        Args:
            allowed_tools: 允许暴露给智能体的工具名称白名单；为空时返回全部注册工具。

        Returns:
            标准 MCP 工具描述字典列表。
        """
        allow_set = set(allowed_tools) if allowed_tools is not None else None
        specs: List[Dict[str, Any]] = []
        for tool_def in self.server.list_tools():
            if allow_set is not None and tool_def.name not in allow_set:
                continue
            specs.append(
                {
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "inputSchema": tool_def.inputSchema,
                }
            )
        return specs

    def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        remaining_seconds: Optional[float] = None,
        cancel_event: Optional[threading.Event] = None,
        preview_chars: int = 3200,
    ) -> ToolOutcome:
        """通过 MCP tools/call 执行工具并返回归一化 ToolOutcome。

        Args:
            tool_name: 目标 MCP 工具名称。
            arguments: 结构化调用参数。
            remaining_seconds: 剩余可用全局时间预算（秒）。
            cancel_event: 任务取消事件信号。
            preview_chars: 输出摘要最大字节/字符上限。

        Returns:
            经由共享超时执行器返回的归一化 ToolOutcome。
        """
        accepts_timeout = any(
            tool.name == tool_name and "timeout" in tool.inputSchema.get("properties", {})
            for tool in self.server.list_tools()
        )
        call_args = clamp_timeout_args(arguments, remaining_seconds=remaining_seconds, inject_timeout=accepts_timeout)

        def _invoke_mcp() -> Any:
            mcp_res = self.server.call_tool(tool_name, call_args)
            raw_text = mcp_res.content[0].text if mcp_res.content else ""
            if mcp_res.isError:
                lowered = raw_text.lower()
                if "timeout" in lowered or "timed out" in lowered or "执行期限" in lowered:
                    raise TimeoutError(raw_text or "MCP timeout")
                raise _McpToolCallError(raw_text or "Unknown MCP error")
            try:
                return json.loads(raw_text) if raw_text else []
            except Exception:
                return {"content": raw_text} if raw_text else []

        with node_span("mcp.call_tool", "TOOL", {SpanAttributes.TOOL_NAME: tool_name}) as span:
            outcome = execute_tool_with_outcome(
                tool_name,
                _invoke_mcp,
                {},
                remaining_seconds=remaining_seconds,
                cancel_event=cancel_event,
                preview_chars=preview_chars,
            )
            if span is not None:
                span.set_attribute("tool.status", outcome.status)
                span.set_attribute("tool.dispatched", outcome.dispatched)
                span.set_attribute("tool.result_count", outcome.data_count)
                if outcome.status in ("error", "timeout", "cancelled"):
                    if not any(e.name == EVENT_NAME and e.attributes.get("outcome") == "failed" for e in span.events):
                        record_diagnostic(f"tool.{outcome.status}", "failed", publish_now=False)
                    span.set_attribute("error.type", outcome.status)
                    span.set_status(StatusCode.ERROR)
            return outcome
