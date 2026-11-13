"""智能体核心循环与调度引擎。

包含通信协议定义、工作区上下文隔离、异步事件总线、执行器与生命周期状态机。
严格遵循 Google Python Style 规范。
"""

from .events import (
    AgentEventHub,
    AgentEventType,
    HookEventType,
    ToolCallAction,
    ToolResultAction,
    default_event_hub,
)
from .protocol import (
    SubagentState,
    SubagentState as AgentState,
    Message,
    MessageType,
    SubagentDefinition,
    SubagentResult,
    WorkspaceMode,
)
from .context import ArtifactMetadata, SubagentContext
from .bus import MessageBus, default_bus
from .manager import SubagentManager, ManagedSubagent
from .scheduler import AgentScheduler, AgentScheduler as ReactiveScheduler
from .runner import SubagentRunner
from .rules import RulesEngine
from ..tools.search_web import search_web
from ..compaction.compactor import ContextCompactor
from ..memory.store import LongTermMemoryStore
from ..memory.transcript import TranscriptLogger, TranscriptStep
from ..skills.registry import Skill, SkillRegistry
from ..commands.registry import SlashCommand, SlashCommandRegistry


from .registry import (
    SubagentRegistry,
    get_default_registry,
    set_default_registry,
)

default_registry = get_default_registry()


__all__ = [
    "AgentEventHub",
    "AgentEventType",
    "HookEventType",
    "ToolCallAction",
    "ToolResultAction",
    "default_event_hub",
    "AgentState",
    "SubagentState",
    "ArtifactMetadata",
    "Message",
    "MessageType",
    "SubagentDefinition",
    "SubagentResult",
    "WorkspaceMode",
    "SubagentContext",
    "MessageBus",
    "default_bus",
    "SubagentManager",
    "ManagedSubagent",
    "AgentScheduler",
    "ReactiveScheduler",
    "SubagentRunner",
    "SubagentRegistry",
    "default_registry",
    "search_web",
    "RulesEngine",
    "ContextCompactor",
    "LongTermMemoryStore",
    "TranscriptLogger",
    "TranscriptStep",
    "Skill",
    "SkillRegistry",
    "SlashCommand",
    "SlashCommandRegistry",
]