"""多智能体协议与数据契约定义。

自研多智能体架构设计规范：
1. 子智能体角色与能力定义 (SubagentDefinition)；
2. 反应式消息总线事件对象 (Message, MessageType)；
3. 子智能体运行结果与工件包装 (SubagentResult)；
4. 生命周期状态与隔离级别枚举 (SubagentState, WorkspaceMode)；
5. 注释与接口定义遵循 Google Python Style 规范。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class SubagentState(str, Enum):
    """子智能体生命周期状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


class WorkspaceMode(str, Enum):
    """工作区隔离模式（支持 inherit / branch / share 模式）。"""

    INHERIT = "inherit"  # 共享父级工作区上下文
    BRANCH = "branch"   # 完全独立的分支沙箱（隔离 scratch 与临时文件）
    SHARE = "share"     # 共享存储只读引用，独立工作区写入


class MessageType(str, Enum):
    """智能体通信消息类型。"""

    TASK = "task"           # 父级派发任务
    PROGRESS = "progress"   # 子级实时进度上报
    RESULT = "result"       # 任务完成结果汇报
    ERROR = "error"         # 任务执行异常通知
    CONTROL = "control"     # 控制信号（中断/重试/撤回）
    INTER_AGENT = "inter_agent"  # 智能体间交互或人类干预消息


@dataclass
class Message:
    """智能体事件消息对象。

    Attributes:
        sender_id: 发送方智能体 ID。
        recipient_id: 接收方智能体 ID（广播时为 'broadcast'）。
        msg_type: 消息类型。
        content: 文本正文或结构化概要。
        metadata: 附加元数据（如 Token 统计、用时、数据索引）。
        message_id: 消息唯一标识。
        created_at: 消息生成时间戳。
    """

    sender_id: str
    recipient_id: str
    msg_type: MessageType
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)


@dataclass
class SubagentDefinition:
    """子智能体类型定义契约。

    Attributes:
        name: 智能体类型唯一名称（统一采用 _agent 后缀，如 web_agent、main_agent）。
        role: 角色称谓（2-5 字短语，如 '全网市场调研专员'）。
        description: 职责与应用场景说明。
        system_prompt: 角色系统设定与核心原则。
        tools: 授权该智能体可调用的工具函数列表。
        model_tier: 默认指派的模型梯度 (fast / strong / vision)。
        enable_write: 是否允许写入本地工件与 scratch 存储。
        enable_subagent: 是否允许该子智能体继续向下递归派生。
    """

    name: str
    role: str
    description: str
    system_prompt: str
    tools: List[Callable[..., Any]] = field(default_factory=list)
    model_tier: str = "fast"
    enable_write: bool = True
    enable_subagent: bool = False


@dataclass
class SubagentResult:
    """子智能体执行终态产物。

    Attributes:
        subagent_id: 子智能体实例 ID。
        type_name: 智能体类型名。
        role: 角色名称。
        state: 终态状态 (completed / failed / killed)。
        summary: 高密度结构化摘要（直接返回给父 Agent，防止海量原始数据污染上下文）。
        data: 结构化解析数据（如商品字典列表、价格统计）。
        artifacts: 生成的高持久化工件列表（如 Markdown 报告、图表规格）。
        scratch_files: 落盘在 scratch 目录的原始抓取数据文件路径列表。
        error: 失败时的异常详细说明。
        elapsed_seconds: 运行总耗时（秒）。
        tokens_used: 消耗的 Token 总量估算。
    """

    subagent_id: str
    type_name: str
    role: str
    state: SubagentState
    summary: str
    data: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    scratch_files: List[str] = field(default_factory=list)
    error: str = ""
    elapsed_seconds: float = 0.0
    tokens_used: int = 0
