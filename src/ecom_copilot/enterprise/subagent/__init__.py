"""自研多智能体协同引擎核心包（TaoZhi Multi-Agent Framework）。

导出完整的多智能体协议、双轨记忆、分层规则、渐进技能、斜杠指令、生命周期管理与反应式调度器。
"""

from .bus import MessageBus
from .commands import SlashCommand, SlashCommandRegistry
from .context import ArtifactMetadata, SubagentContext
from .manager import ManagedSubagent, SubagentManager
from .memory.compactor import ContextCompactor
from .memory.store import LongTermMemoryStore
from .memory.transcript import TranscriptLogger, TranscriptStep
from .protocol import (
    Message,
    MessageType,
    SubagentDefinition,
    SubagentResult,
    SubagentState,
    WorkspaceMode,
)
from .registry import SubagentRegistry, default_registry
from .rules import RuleDocument, RulesEngine
from .runner import SubagentRunner
from .scheduler import AgentScheduler, ScheduledJob
from .skills import Skill, SkillRegistry
from .tools import (
    extract_search_keyword,
    fetch_web_page,
    search_1688_wholesale,
    search_web,
)

__all__ = [
    # 消息与总线
    "MessageBus",
    "Message",
    "MessageType",
    # 上下文与工件
    "SubagentContext",
    "ArtifactMetadata",
    "WorkspaceMode",
    # 智能体协议与注册
    "SubagentDefinition",
    "SubagentResult",
    "SubagentState",
    "SubagentRegistry",
    "default_registry",
    # 执行与生命周期管理
    "SubagentRunner",
    "SubagentManager",
    "ManagedSubagent",
    # 定时调度
    "AgentScheduler",
    "ScheduledJob",
    # 双轨记忆与压实
    "TranscriptLogger",
    "TranscriptStep",
    "ContextCompactor",
    "LongTermMemoryStore",
    # 规则与技能
    "RulesEngine",
    "RuleDocument",
    "SkillRegistry",
    "Skill",
    # 斜杠指令
    "SlashCommandRegistry",
    "SlashCommand",
    # 工具
    "search_web",
    "fetch_web_page",
    "search_1688_wholesale",
    "extract_search_keyword",
]
