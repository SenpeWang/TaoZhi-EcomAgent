"""淘智会话管理与上下文投影模块。"""

from .session_manager import SessionManager
from .projection import build_session_projection

__all__ = ["SessionManager", "build_session_projection"]