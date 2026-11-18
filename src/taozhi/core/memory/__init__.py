"""多租户长效记忆库与上下文检索辅助。

提供租户物理数据隔离、并发排他加锁与原子替换写入。
严格遵循 Google Python Style 规范。
"""

from .store import LongTermMemoryStore
from .service import get_memory_store, get_followup_context

__all__ = [
    "LongTermMemoryStore",
    "get_memory_store",
    "get_followup_context",
]
