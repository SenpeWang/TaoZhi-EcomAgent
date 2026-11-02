"""淘智工具执行底座与统一抽象。

定义工具的标准元数据规范与统一执行接口。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolDefinition:
    """工具定义规范。"""
    name: str
    description: str
    func: Callable[..., Any]
    parameters_schema: Dict[str, Any] = field(default_factory=dict)

    def __call__(self, *args, **kwargs) -> Any:
        return self.func(*args, **kwargs)
