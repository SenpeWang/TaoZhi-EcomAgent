"""MCP 基础设施层：提供标准 Model Context Protocol 服务端与智能体桥接器。"""
from .adapter import McpClientAdapter
from .protocol import McpCallToolResult, McpContentItem, McpToolDefinition
from .server import EcommerceMcpServer, default_mcp_server

__all__ = [
    "EcommerceMcpServer",
    "McpCallToolResult",
    "McpClientAdapter",
    "McpContentItem",
    "McpToolDefinition",
    "default_mcp_server",
]