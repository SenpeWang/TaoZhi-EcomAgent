"""MCP 智能体工具适配器（McpToolAdapter）。

将任何符合 MCP 协议规范的工具，桥接转接为 TaoZhi 智能体底座的 BaseTool 原生契约。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ...core.tools.base import BaseTool, ToolContext, ToolResult
from ...observability.tracer import get_current_trace
from .protocol import McpToolDefinition
from .server import EcommerceMcpServer, default_mcp_server


class McpToolAdapter(BaseTool):
    """将 MCP 工具适配为 TaoZhi 原生受控 BaseTool 的通用双向桥接器。"""

    def __init__(
        self,
        tool_definition: McpToolDefinition,
        server: Optional[EcommerceMcpServer] = None,
    ) -> None:
        self.name = tool_definition.name
        self.description = tool_definition.description
        self.parameters_schema = tool_definition.inputSchema
        self.server = server or default_mcp_server

    def run(self, params: Dict[str, Any], context: ToolContext) -> ToolResult:
        trace = get_current_trace()
        span_ctx = trace.span(
            f"mcp.tool.{self.name}",
            kind="tool",
            input_data={"tool": self.name, "params": params},
        ) if trace else None

        if span_ctx:
            with span_ctx as s:
                mcp_res = self.server.call_tool(self.name, params)
                s.output_data = {"isError": mcp_res.isError}
        else:
            mcp_res = self.server.call_tool(self.name, params)

        if mcp_res.isError:
            err_text = mcp_res.content[0].text if mcp_res.content else "Unknown MCP error"
            return ToolResult(
                success=False,
                output=err_text,
                actionable_hint=f"MCP 工具 [{self.name}] 执行异常，建议检查入参 Schema 规范。",
            )

        raw_text = mcp_res.content[0].text if mcp_res.content else ""
        try:
            parsed_data = json.loads(raw_text)
        except Exception:
            parsed_data = raw_text

        return ToolResult(
            success=True,
            output=parsed_data,
            actionable_hint=f"MCP 工具 [{self.name}] 成功返回结构化情报数据。",
        )