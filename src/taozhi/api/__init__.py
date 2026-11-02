"""接入层协议与 API 路由：负责外部请求校验、协议转换与统一入口。"""
from .chat import chat_endpoint, router as chat_router

__all__ = ["chat_router", "chat_endpoint"]