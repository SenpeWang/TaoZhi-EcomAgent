"""斜杠指令系统（Slash Commands System）。

自研斜杠指令交互架构：
1. 提供标准指令集：/compact, /learn, /plan, /goal, /teamwork, /grill-me, /schedule, /browser；
2. 规范化提示词注入（<slash_commands> 架构块）；
3. 支持指令解析与专用 Handler 调度执行；
4. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..compaction.compactor import ContextCompactor
from ..memory.store import LongTermMemoryStore
from ..memory.transcript import TranscriptLogger


class SlashCommand:
    """表示一个可执行或可推荐的斜杠指令。"""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        """初始化斜杠指令。

        Args:
            name: 指令名称（带斜杠，如 /compact）。
            description: 用户场景说明与推荐时机。
            handler: 实际执行函数。
        """
        self.name = name if name.startswith("/") else f"/{name}"
        self.description = description
        self.handler = handler

    def execute(self, argument: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行该指令逻辑。

        Args:
            argument: 用户在指令后附带的参数字符串。
            context: 运行时上下文环境。

        Returns:
            Dict[str, Any]: 执行响应结果。
        """
        if self.handler:
            return self.handler(argument, context)
        return {"status": "ok", "message": f"Command {self.name} received (argument: {argument})"}


class SlashCommandRegistry:
    """管理全局斜杠指令的注册、匹配与提示词声明生成。"""

    def __init__(
        self,
        memory_store: Optional[LongTermMemoryStore] = None,
        compactor: Optional[ContextCompactor] = None,
    ) -> None:
        """初始化指令注册中心。

        Args:
            memory_store: 长效记忆库实例。
            compactor: 上下文压实器实例。
        """
        self.memory_store = memory_store or LongTermMemoryStore()
        self.compactor = compactor or ContextCompactor()
        self._commands: Dict[str, SlashCommand] = {}
        self._register_default_commands()

    def _register_default_commands(self) -> None:
        """注册全部核心斜杠指令。"""

        # 1. /compact
        def _handle_compact(arg: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
            logger: Optional[TranscriptLogger] = ctx.get("transcript_logger")
            if not logger:
                return {"status": "error", "error": "No active transcript logger in context"}
            summary = self.compactor.compact(
                transcript_logger=logger,
                active_subagents=ctx.get("active_subagents"),
            )
            return {"status": "ok", "summary": summary}

        self.register(
            SlashCommand(
                name="/compact",
                description="Recommend this when the conversation is getting long and the user wants to compact or summarize the context to free up the context window.",
                handler=_handle_compact,
            )
        )

        # 2. /learn
        def _handle_learn(arg: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
            if not arg.strip():
                return {"status": "error", "error": "Please provide the rule or preference to learn"}
            category = ctx.get("category", "learned_preference")
            entry = self.memory_store.learn_rule(
                rule_text=arg,
                category=category,
                source="slash_command_learn",
            )
            return {"status": "ok", "entry": entry, "message": f"Successfully learned rule: {arg}"}

        self.register(
            SlashCommand(
                name="/learn",
                description="Recommend this when the user has corrected the agent or solved a complex setup and wants the agent to persist this behavior for future tasks.",
                handler=_handle_learn,
            )
        )

        # 3. /plan
        def _handle_plan(arg: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "status": "ok",
                "phase": "planning",
                "goal": arg,
                "message": f"Initiating structured planning for: {arg}",
            }

        self.register(
            SlashCommand(
                name="/plan",
                description="Recommend this when the task is complex and requires careful step-by-step planning before execution.",
                handler=_handle_plan,
            )
        )

        # 4. /goal
        self.register(
            SlashCommand(
                name="/goal",
                description="Recommend this when the user wants to run a long-running task (e.g., overnight) and wants the agent to be extra thorough and not stop until the goal is fully achieved.",
            )
        )

        # 5. /teamwork
        self.register(
            SlashCommand(
                name="/teamwork",
                description="Recommend this when the user has a large project that would benefit from a team of autonomous agents working together.",
            )
        )

        # 6. /grill-me
        self.register(
            SlashCommand(
                name="/grill-me",
                description="Recommend this when the user wants to align on a plan through an interactive interview to resolve design decisions.",
            )
        )

        # 7. /schedule
        self.register(
            SlashCommand(
                name="/schedule",
                description="Recommend this when the user wants to run an instruction on a recurring schedule or set a one-time timer.",
            )
        )

        # 8. /browser
        self.register(
            SlashCommand(
                name="/browser",
                description="Recommend this when the user's task involves web browsing, searching the web, or interacting with web applications.",
            )
        )

    def register(self, command: SlashCommand) -> None:
        """注册一个斜杠指令。

        Args:
            command: SlashCommand 实例。
        """
        self._commands[command.name] = command

    def parse(self, user_input: str) -> Optional[Tuple[SlashCommand, str]]:
        """检查用户输入是否为斜杠指令调用。

        Args:
            user_input: 用户原始输入字符串。

        Returns:
            Optional[Tuple[SlashCommand, str]]: 匹配成功返回 (指令对象, 参数串)，否则 None。
        """
        text = user_input.strip()
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        cmd = self._commands.get(cmd_name)
        if cmd:
            return cmd, arg
        return None

    def build_slash_commands_prompt_block(self) -> str:
        """生成自研规范的 <slash_commands> 提示词上下文说明。

        Returns:
            str: 格式化的 <slash_commands> 上下文块。
        """
        lines = [
            "<slash_commands>",
            "Slash commands are user-facing shortcuts in the chat UI that automate complex workflows or trigger specialized agent behaviors.",
            "",
            "You cannot execute these commands yourself. Your role is to recommend them to the user when they are a good fit for the task at hand, encouraging the user to explore and trigger them.",
            "",
            "To recommend a slash command, suggest it clearly in your response (e.g., \"You can use the `/goal` command to...\").",
            "",
            "Available slash commands you can recommend to the user:",
        ]

        for cmd in self._commands.values():
            lines.append(f"- {cmd.name}: {cmd.description}")

        lines.append("</slash_commands>")
        return "\n".join(lines)
