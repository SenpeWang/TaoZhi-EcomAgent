"""MCP 智能体客户端适配器（McpClientAdapter）。

将标准 MCP 协议（tools/list 与 tools/call）封装为受控的多智能体工具客户端：
1. 动态从 EcommerceMcpServer 发现合法工具元数据与 JSON Schema；
2. 统一通过 MCP tools/call 执行工具，并注入超时预算、取消中断、Phoenix Span 追踪与结构化 ToolOutcome 转换。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional, Sequence

from ...core.tools.outcome import ToolOutcome, ToolStatus
from ...core.tools.truncate import truncate_text
from ...observability.tracing import node_span
from .protocol import McpCallToolResult
from .server import EcommerceMcpServer, default_mcp_server


class McpClientAdapter:
    """面向多智能体系统的标准 MCP 协议工具客户端。"""

    def __init__(self, server: Optional[EcommerceMcpServer] = None) -> None:
        self.server = server or default_mcp_server

    def list_tool_specs(self, allowed_tools: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """通过 MCP tools/list 获取受允许工具的元数据与 Schema，供智能体提示词注入。"""
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
        """通过 MCP tools/call 执行工具并返回结构化 ToolOutcome。"""
        if cancel_event is not None and cancel_event.is_set():
            return ToolOutcome(
                tool_name=tool_name,
                status="cancelled",
                error_reason="任务已被取消，跳过 MCP 工具执行",
            )

        if remaining_seconds is not None and remaining_seconds <= 0:
            return ToolOutcome(
                tool_name=tool_name,
                status="timeout",
                error_reason="全局剩余时间预算耗尽，跳过 MCP 工具执行",
            )

        call_args = dict(arguments or {})
        if remaining_seconds is not None:
            bounded = max(0.5, float(remaining_seconds))
            requested = call_args.get("timeout")
            call_args["timeout"] = min(float(requested), bounded) if requested is not None else min(30.0, bounded)

        t0 = time.perf_counter()
        try:
            with node_span(f"mcp.tool.{tool_name}", {"tool": tool_name}):
                if remaining_seconds is not None:
                    wait_limit = max(0.5, float(remaining_seconds))
                    pool = ThreadPoolExecutor(max_workers=1)
                    fut = pool.submit(self.server.call_tool, tool_name, call_args)
                    try:
                        mcp_res: McpCallToolResult = fut.result(timeout=wait_limit)
                    finally:
                        pool.shutdown(wait=False, cancel_futures=True)
                else:
                    mcp_res = self.server.call_tool(tool_name, call_args)
        except (TimeoutError, FuturesTimeoutError) as exc:
            return ToolOutcome(
                tool_name=tool_name,
                status="timeout",
                elapsed=round(time.perf_counter() - t0, 2),
                error_reason=f"MCP 工具执行超时: {exc}",
            )
        except Exception as exc:
            return ToolOutcome(
                tool_name=tool_name,
                status="error",
                elapsed=round(time.perf_counter() - t0, 2),
                error_reason=f"MCP 调用异常: {type(exc).__name__}: {exc}",
            )

        elapsed = round(time.perf_counter() - t0, 2)
        if cancel_event is not None and cancel_event.is_set():
            return ToolOutcome(
                tool_name=tool_name,
                status="cancelled",
                elapsed=elapsed,
                error_reason="MCP 工具执行期间收到取消请求",
            )

        raw_text = mcp_res.content[0].text if mcp_res.content else ""
        if mcp_res.isError:
            lowered = raw_text.lower()
            status: ToolStatus = "timeout" if ("timeout" in lowered or "timed out" in lowered) else "error"
            return ToolOutcome(
                tool_name=tool_name,
                status=status,
                elapsed=elapsed,
                error_reason=raw_text or "Unknown MCP error",
            )

        try:
            parsed = json.loads(raw_text) if raw_text else []
        except Exception:
            parsed = {"content": raw_text} if raw_text else []

        if isinstance(parsed, list):
            items = [x for x in parsed if isinstance(x, dict)]
            if not items and parsed:
                items = [{"value": str(x)} for x in parsed]
            status = "ok" if items else "empty"
            preview = truncate_text(json.dumps(items[:10], ensure_ascii=False), max_bytes=preview_chars).content
            return ToolOutcome(
                tool_name=tool_name,
                status=status,
                items=items,
                text_preview=preview,
                data_count=len(items),
                elapsed=elapsed,
            )

        if isinstance(parsed, dict):
            if parsed.get("error"):
                return ToolOutcome(
                    tool_name=tool_name,
                    status="error",
                    items=[parsed],
                    text_preview=truncate_text(json.dumps(parsed, ensure_ascii=False), max_bytes=preview_chars).content,
                    data_count=0,
                    elapsed=elapsed,
                    error_reason=str(parsed.get("error")),
                )
            content = str(parsed.get("content") or parsed.get("text") or "")
            status = "ok" if (content.strip() or len(parsed) > 0) else "empty"
            preview = truncate_text(json.dumps(parsed, ensure_ascii=False), max_bytes=preview_chars).content
            return ToolOutcome(
                tool_name=tool_name,
                status=status,
                items=[parsed] if status == "ok" else [],
                text_preview=preview,
                data_count=1 if status == "ok" else 0,
                elapsed=elapsed,
            )

        return ToolOutcome(tool_name=tool_name, status="empty", elapsed=elapsed)
