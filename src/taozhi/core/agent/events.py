"""PiAgent 规范的双管道事件体系（Two-Pipeline Event System）。

选管道一句话铁律：
你的代码要不要改变 Agent 的行为？
- 要（拦截、改参数、改消息、存状态）➔ 写扩展，走管道 B（hooks.on）。Agent 会等你、读你的返回值。
- 不要（打日志、推前端、记统计）➔ 用管道 A（bus.subscribe）更轻。Agent 不等你、不读返回值。

核心架构：
1. 管道 A：11 种只读核心通知事件（封闭、只读、可穷尽检查、Fire-and-forget）；
2. 管道 B：2 种核心拦截事件（开放、可改写、责任链、串行等待返回值）；
   - tool_call: 执行前权限闸门（裸 await，无 try/catch，容错跟风险走，阻断即短路）；
   - tool_result: 执行后统一出口（顺序责任链，有 try/catch，下游统一受益）；
3. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentEventType(str, Enum):
    """PiAgent 11 种只读核心通知事件（管道 A）。"""
    AGENT_START = "agent_start"                      # 一次 Prompt 运行的开始括号
    AGENT_END = "agent_end"                          # 运行结束括号，携带本轮统计
    TURN_START = "turn_start"                        # 单轮决策迭代开始
    TURN_END = "turn_end"                            # 单轮迭代结束，停止判断挂点
    MESSAGE_START = "message_start"                  # 消息流式输出开始
    MESSAGE_UPDATE = "message_update"                # 高频增量 Delta (UI 逐字渲染)
    MESSAGE_END = "message_end"                      # 消息输出终态 (持久化只认此事件)
    TOOL_EXECUTION_START = "tool_execution_start"    # 工具底层执行启动纯通知
    TOOL_EXECUTION_UPDATE = "tool_execution_update"  # 工具执行过程进度汇报 (onUpdate)
    TOOL_EXECUTION_END = "tool_execution_end"        # 工具底层执行结束纯通知
    TOOL_EXEC_START = "tool_execution_start"         # 别名
    TOOL_EXEC_UPDATE = "tool_execution_update"       # 别名
    TOOL_EXEC_END = "tool_execution_end"             # 别名
    ERROR = "error"                                  # 运行时异常告警通知


class HookEventType(str, Enum):
    """PiAgent 2 种行为拦截事件（管道 B：改变 Agent 行为）。"""
    TOOL_CALL = "tool_call"       # 执行前拦截：模型想做 ➔ 允许做，返回 block 立即短路
    TOOL_RESULT = "tool_result"   # 执行后改写：进入上下文前统一出口，责任链脱敏与截断


@dataclass
class ToolCallAction:
    """管道 B: tool_call 前置拦截响应。"""
    block: bool = False
    reason: Optional[str] = None
    override_args: Optional[Dict[str, Any]] = None


@dataclass
class ToolResultAction:
    """管道 B: tool_result 后置改写载荷（责任链传递）。"""
    content: str
    details: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    truncated: bool = False


class AgentEventHub:
    """双管道事件中枢：统一管理只读通知（管道 A）与行为拦截扩展（管道 B）。"""

    def __init__(self) -> None:
        # 管道 A 观察者容器：event_type -> list of callbacks (只读，Agent 不等待返回值)
        self._notification_subscribers: Dict[str, List[Callable[..., None]]] = {}
        # 管道 B 拦截器容器：hook_name -> list of hook handlers (干预，Agent 等待返回值)
        self._tool_call_hooks: List[Callable[[str, Any, Any], Optional[ToolCallAction]]] = []
        self._tool_result_hooks: List[Callable[[str, ToolResultAction, Any], Optional[ToolResultAction]]] = []

    # ==================== 管道 A：只读订阅通知 (subscribe) ====================

    def subscribe(self, event_type: str | AgentEventType, callback: Callable[..., None]) -> None:
        """注册只读事件监听器（管道 A：打日志、推前端、记统计）。

        特性：Agent 不等、不读返回值，单向通知，异常隔离。
        """
        key = event_type.value if hasattr(event_type, "value") else str(event_type)
        self._notification_subscribers.setdefault(key, []).append(callback)

    def emit_notification(self, event_type: str | AgentEventType, **payload: Any) -> None:
        """广播只读通知事件（管道 A）。"""
        key = event_type.value if hasattr(event_type, "value") else str(event_type)
        for cb in self._notification_subscribers.get(key, []):
            try:
                cb(**payload)
            except Exception as exc:
                logger.debug("管道 A 只读监听器异常隔离: %s", exc)

    # ==================== 管道 B：行为拦截与改写 (on / hooks) ====================

    def on(
        self,
        hook_type: str | HookEventType,
        handler: Callable[..., Any],
    ) -> None:
        """注册行为拦截扩展（管道 B：拦截、改参数、改消息、存状态）。

        特性：Agent 会等待执行，读取返回值以改变后续决策。
        """
        key = hook_type.value if hasattr(hook_type, "value") else str(hook_type)
        if key == HookEventType.TOOL_CALL.value or key == "tool_call":
            self._tool_call_hooks.append(handler)
        elif key == HookEventType.TOOL_RESULT.value or key == "tool_result":
            self._tool_result_hooks.append(handler)
        else:
            raise ValueError(f"未知的拦截钩子类型: {hook_type}，只支持 tool_call 或 tool_result。")

    def execute_tool_call_hooks(
        self,
        tool_name: str,
        arguments: Any,
        context: Any,
    ) -> ToolCallAction:
        """触发 tool_call 执行前拦截（权限闸门）。

        设计思想：把“模型想做”和“允许做”拆成两件事。
        【关键规范】：无 try/catch 包裹（裸调用）。闸门代码出错就让它崩溃阻断（Fail-Closed），
        绝不能静默放行高危操作——容错策略跟着风险走。
        """
        for hook in self._tool_call_hooks:
            # 裸调用：不加 try/catch，容错跟着风险走！
            res = hook(tool_name, arguments, context)
            if res and isinstance(res, ToolCallAction) and res.block:
                return res
        return ToolCallAction(block=False)

    def execute_tool_result_hooks(
        self,
        tool_name: str,
        initial_action: ToolResultAction,
        context: Any,
    ) -> ToolResultAction:
        """触发 tool_result 执行后改写（统一后处理出口）。

        设计思想：在工具结果流入上下文前卡点，下游环节（模型上下文、JSONL持久化、UI）统一受益。
        【关键规范】：顺序责任链 + 带 try/catch。某个改写器坏了跳过继续，因为结果本身无害。
        """
        current_action = initial_action
        for hook in self._tool_result_hooks:
            try:
                res = hook(tool_name, current_action, context)
                if res and isinstance(res, ToolResultAction):
                    current_action = res
            except Exception as exc:
                logger.warning("tool_result 改写器异常，容错跳过继续: %s", exc)

        return current_action


# 全局默认单例
default_event_hub = AgentEventHub()