"""面向非人类消费者的受控工具抽象基类：具备有界输出、错误引导与演练能力。"""
from __future__ import annotations

import abc
import time
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ToolContext(BaseModel):
    """工具调用的上下文环境与安全边界。"""
    tenant_id: str = "default"
    user_id: Optional[str] = None
    security_level: int = 0
    dry_run: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具调用的标准结构化出参。"""
    success: bool
    output: Any
    actionable_hint: Optional[str] = None
    truncated: bool = False
    duration_ms: float = 0.0


class BaseTool(abc.ABC):
    """工业级工具基类：面向模型与 Agent 消费。"""

    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = {}
    max_output_chars: int = 3000

    @abc.abstractmethod
    def run(self, params: Dict[str, Any], context: ToolContext) -> ToolResult:
        """执行具体工具逻辑。子类实现。"""
        pass

    def __call__(self, params: Dict[str, Any], context: Optional[ToolContext] = None) -> ToolResult:
        ctx = context or ToolContext()
        start = time.time()
        if ctx.dry_run:
            return ToolResult(
                success=True,
                output={"status": "dry_run_validated", "params": params},
                actionable_hint="Dry run 演练成功，未对持久化状态造成修改。",
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        try:
            res = self.run(params, ctx)
            res.duration_ms = round((time.time() - start) * 1000, 2)
            # 输出有界截断
            if isinstance(res.output, str) and len(res.output) > self.max_output_chars:
                res.output = res.output[: self.max_output_chars] + "\n...[输出超长已分页截断]"
                res.truncated = True
            return res
        except Exception as exc:
            return ToolResult(
                success=False,
                output=str(exc),
                actionable_hint=f"工具执行异常: {type(exc).__name__}，建议核对参数类型或收窄检索范围后重试。",
                duration_ms=round((time.time() - start) * 1000, 2),
            )