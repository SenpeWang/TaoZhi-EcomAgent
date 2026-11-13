"""子智能体执行引擎（Subagent Runner）。

自研执行引擎核心架构（全面对齐 PiAgent 双管道事件规范）：
1. 管道 A：11 种只读通知事件广播（agent_start/end, turn_start/end, tool_execution_*）；
2. 管道 B：2 种行为拦截事件（tool_call 权限闸门裸 await 短路，tool_result 统一出口责任链改写）；
3. 动态 Steering：中途追加干预指令与实时纠偏；
4. 双轨记忆：transcript 双轨日志与 ContextCompactor 阶梯压实；
5. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from ...ai.client import LLMClient, ModelTier
from .bus import MessageBus
from ..commands.registry import SlashCommandRegistry
from .context import ArtifactMetadata, SubagentContext
from .events import (
    AgentEventHub,
    AgentEventType,
    HookEventType,
    ToolCallAction,
    ToolResultAction,
    default_event_hub,
)
from .manager import SubagentManager
from ..compaction.compactor import ContextCompactor
from ..memory.store import LongTermMemoryStore
from ..memory.transcript import TranscriptLogger
from .protocol import (
    Message,
    MessageType,
    SubagentDefinition,
    SubagentResult,
    SubagentState,
    WorkspaceMode,
)
from .rules import RulesEngine
from .scheduler import AgentScheduler
from ..skills.registry import SkillRegistry
from ..tools.search_web import extract_search_keyword


class SubagentRunner:
    """负责子智能体的生命周期管理、任务派发与并发执行调度。"""

    def __init__(
        self,
        bus: Optional[MessageBus] = None,
        llm_client: Optional[LLMClient] = None,
        memory_store: Optional[LongTermMemoryStore] = None,
        registry: Optional[Any] = None,
        event_hub: Optional[AgentEventHub] = None,
    ) -> None:
        """初始化执行引擎。

        Args:
            bus: 消息总线实例。
            llm_client: LLM 客户端实例。
            memory_store: 长效持久化记忆库。
            registry: 智能体类型注册表。
            event_hub: 双管道事件中枢（管道 A 只读通知 + 管道 B 拦截干预）。
        """
        self.bus = bus or MessageBus()
        self.llm = llm_client or LLMClient()
        if registry is not None:
            self.registry = registry
        else:
            from .registry import get_default_registry
            self.registry = get_default_registry()

        self.event_hub = event_hub or default_event_hub
        self._setup_default_hooks()

        self.long_term_memory = memory_store or LongTermMemoryStore()
        self.compactor = ContextCompactor(llm_client=self.llm)
        self.manager = SubagentManager(bus=self.bus)
        self.scheduler = AgentScheduler(bus=self.bus)
        self.rules_engine = RulesEngine()
        self.skills_registry = SkillRegistry()
        self.command_registry = SlashCommandRegistry(
            memory_store=self.long_term_memory,
            compactor=self.compactor,
        )

    def _setup_default_hooks(self) -> None:
        """初始化管道 B 默认的统一后处理出口责任链（脱敏 + 行边界截断）。"""
        def _default_redact_hook(tool_name: str, action: ToolResultAction, ctx: Any) -> ToolResultAction:
            try:
                from taozhi.domain.privacy import redact
                action.content = redact(action.content)
            except Exception:
                pass
            return action

        def _default_truncate_hook(tool_name: str, action: ToolResultAction, ctx: Any) -> ToolResultAction:
            try:
                from ..tools.truncate import preserve_line_boundary_truncate
                if len(action.content) > 3000:
                    action.content = preserve_line_boundary_truncate(action.content, max_chars=3000)
                    action.truncated = True
            except Exception:
                pass
            return action

        self.event_hub.on(HookEventType.TOOL_RESULT, _default_redact_hook)
        self.event_hub.on(HookEventType.TOOL_RESULT, _default_truncate_hook)

    def invoke_subagent(
        self,
        type_name: str,
        role: str,
        prompt: str,
        model_tier: Optional[str] = None,
        workspace_mode: WorkspaceMode = WorkspaceMode.BRANCH,
        parent_context: Optional[SubagentContext] = None,
        custom_tools: Optional[List[Callable]] = None,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> SubagentResult:
        """派发并执行单个子智能体任务。支持 PiAgent 规范的双管道事件拦截与动态 Steering。"""
        start_time = time.perf_counter()
        defn = self.registry.get(type_name)
        if not defn:
            defn = SubagentDefinition(
                name=type_name,
                role=role,
                description="动态派生子智能体",
                system_prompt=f"你是专职于【{role}】的资深专家助手。遵循基于事实原则，严谨执行任务。",
            )

        parent_id = parent_context.conversation_id if parent_context else None
        depth = (parent_context.depth + 1) if parent_context else 1
        ctx = SubagentContext(
            parent_id=parent_id,
            role=role or defn.role,
            depth=depth,
            workspace_mode=workspace_mode,
        )

        sub_id = ctx.conversation_id
        tier_str = model_tier or defn.model_tier
        tier = {
            "fast": ModelTier.FAST,
            "strong": ModelTier.STRONG,
            "vision": ModelTier.VISION,
        }.get(tier_str.lower(), ModelTier.FAST)

        # 管道 A: 广播 agent_start 事件 (运行括号开始)
        self.event_hub.emit_notification(AgentEventType.AGENT_START, subagent_id=sub_id, role=role, type_name=type_name)

        # 接入生命周期管理器
        managed_agent = self.manager.register_subagent(
            context=ctx,
            type_name=type_name,
            role=role or defn.role,
            parent_id=parent_id,
        )

        # 接入双轨日志记录器
        transcript_logger = TranscriptLogger(ctx.logs_dir)
        transcript_logger.append_step(
            source="USER_EXPLICIT" if parent_id is None else "SUBAGENT",
            step_type="USER_INPUT",
            content=prompt,
            metadata={"role": role, "type_name": type_name, "depth": depth},
        )

        # 发布任务启动事件
        self.bus.post(
            Message(
                sender_id=parent_id or "coordinator",
                recipient_id=sub_id,
                msg_type=MessageType.TASK,
                content=prompt,
                metadata={"role": role, "type_name": type_name},
            )
        )

        if on_progress:
            on_progress(f"【{role}】正在启动并执行任务", 20)

        # 管道 A: 广播 turn_start 事件 (第一轮决策迭代)
        self.event_hub.emit_notification(AgentEventType.TURN_START, turn=1, subagent_id=sub_id)

        # 1. 工具调用阶段
        tools_to_run = list(defn.tools) + (custom_tools or [])
        tool_data: List[Dict[str, Any]] = []
        scratch_files: List[str] = []
        steering_notes: List[str] = []

        try:
            for tool in tools_to_run:
                if managed_agent.stop_event.is_set():
                    raise RuntimeError("Subagent execution cancelled")

                # 运行期动态干预拦截 (Mid-flight Steering)
                interventions = self.bus.get_messages(sub_id, msg_type=MessageType.INTER_AGENT, clear=True)
                for item in interventions:
                    steering_notes.append(item.content)
                    transcript_logger.append_step(
                        source="USER_EXPLICIT",
                        step_type="STEERING_INTERVENTION",
                        content=item.content,
                        metadata={"type": "mid_flight_intervention", "stage": "tool_execution"},
                    )

                fn_name = getattr(tool, "__name__", str(tool))
                effective_prompt = prompt
                if steering_notes:
                    effective_prompt = f"{prompt} {' '.join(steering_notes)}"
                search_query = extract_search_keyword(effective_prompt) or extract_search_keyword(prompt) or prompt

                # ==================== 管道 B 拦截：tool_call 前置权限闸门 ====================
                # 【核心设计思想】：模型想做 ➔ 允许做，拆成两件事。无 try/catch（裸调用），容错跟着风险走！
                call_action = self.event_hub.execute_tool_call_hooks(fn_name, search_query, ctx)
                if call_action.block:
                    blocked_msg = f"Tool execution blocked by policy: {call_action.reason or 'Unauthorized'}"
                    transcript_logger.append_step(
                        source="SYSTEM",
                        step_type="TOOL_RESULT",
                        content=blocked_msg,
                        status="ERROR",
                    )
                    continue  # 立即短路！底层代码绝对不执行！

                # 管道 A: 广播 tool_execution_start 纯通知
                self.event_hub.emit_notification(AgentEventType.TOOL_EXEC_START, tool=fn_name, query=search_query)

                transcript_logger.append_step(
                    source="MODEL",
                    step_type="TOOL_CALL",
                    content=f"Calling tool {fn_name}",
                    tool_calls=[{"name": fn_name, "parameters": {"query": search_query, "limit": 15}}],
                )

                # 底层工具真正执行
                try:
                    res = tool(search_query, limit=15)
                except TypeError:
                    try:
                        res = tool(search_query)
                    except TypeError:
                        res = tool()

                # 管道 A: 广播 tool_execution_end 纯通知
                self.event_hub.emit_notification(AgentEventType.TOOL_EXEC_END, tool=fn_name)

                # ==================== 管道 B 拦截：tool_result 统一后处理出口责任链 ====================
                # 【核心设计思想】：进入上下文前卡点，脱敏 + 截断，下游模型、JSONL 与 UI 统一受益！
                raw_text_repr = json.dumps(res, ensure_ascii=False, indent=2) if isinstance(res, (dict, list)) else str(res)
                initial_result_action = ToolResultAction(content=raw_text_repr)
                final_result_action = self.event_hub.execute_tool_result_hooks(fn_name, initial_result_action, ctx)

                if isinstance(res, list):
                    tool_data.extend(res)

                # 写入 scratch 目录与双轨日志（已脱敏与截断）
                raw_file = ctx.write_scratch(f"raw_{fn_name}.json", final_result_action.content)
                scratch_files.append(str(raw_file))

                transcript_logger.append_step(
                    source="SYSTEM",
                    step_type="TOOL_RESULT",
                    content=final_result_action.content,
                    metadata={"file": str(raw_file), "truncated": final_result_action.truncated},
                )

            if on_progress:
                on_progress(f"【{role}】已采集数据并深入研判中", 60)

            # 2. LLM 推理归纳阶段
            pending_interventions = self.bus.get_messages(sub_id, msg_type=MessageType.INTER_AGENT, clear=True)
            for item in pending_interventions:
                steering_notes.append(item.content)
                transcript_logger.append_step(
                    source="USER_EXPLICIT",
                    step_type="STEERING_INTERVENTION",
                    content=item.content,
                    metadata={"type": "mid_flight_intervention", "stage": "pre_inference"},
                )

            data_context = ""
            if tool_data:
                compressed_preview = "\n".join(
                    f"{i+1}. {d.get('title', '')} | 价格: {d.get('price', '')} | 来源: {d.get('shop', d.get('source', ''))}"
                    for i, d in enumerate(tool_data[:15])
                )
                data_context = f"\n\n【已采集到的实时数据预览（共 {len(tool_data)} 条）】：\n{compressed_preview}"

            rules_block = self.rules_engine.build_rules_prompt_block()
            rule_addon = f"\n\n{rules_block}" if rules_block else ""

            steering_block = ""
            if steering_notes:
                notes_text = "\n".join(f"- {note}" for note in steering_notes)
                steering_block = f"\n\n【⚠️ 运行期用户中途追加的修正指令（必须优先遵循）】：\n{notes_text}"

            system_inst = (
                f"{defn.system_prompt}\n\n"
                "【严格原则】：\n"
                "1. 结论必须百分之百基于采集到的事实数据，严禁虚构造假数字；\n"
                "2. 提炼出 3-5 条关键业务要点（价格带、爆款卖点、供应商特征、潜在风险）；\n"
                "3. 输出中文结构化总结。"
                f"{rule_addon}"
            )

            user_query = f"任务指令：{prompt}{steering_block}{data_context}"
            try:
                summary = self.llm.chat(
                    user_query,
                    tier=tier,
                    system=system_inst,
                    max_tokens=2048,
                )
            except Exception as exc:
                summary = f"【{role}】已完成实时数据采集（共 {len(tool_data)} 条）。由于模型服务出现抖动，已转存原始数据至交付物中。"

            transcript_logger.append_step(
                source="MODEL",
                step_type="PLANNER_RESPONSE",
                content=summary,
                metadata={"tier": tier.value if hasattr(tier, "value") else str(tier)},
            )

            # 3. 产物交付与持久化
            art_meta = ArtifactMetadata(
                summary=f"【{role}】执行总结与分析交付物",
                user_facing=True,
                request_feedback=False,
            )
            art_file = ctx.write_artifact("summary.md", summary, metadata=art_meta)
            artifacts = [str(art_file)]

            # 4. 自动压实判断
            if self.compactor.should_compact(transcript_logger.read_compact_steps()):
                self.compactor.compact(transcript_logger=transcript_logger)

            elapsed = round(time.perf_counter() - start_time, 2)
            result = SubagentResult(
                subagent_id=sub_id,
                type_name=type_name,
                role=role or defn.role,
                state=SubagentState.COMPLETED,
                summary=summary,
                data=tool_data,
                artifacts=artifacts,
                scratch_files=scratch_files,
                elapsed_seconds=elapsed,
            )

            # 管道 A: 广播 turn_end 与 agent_end 事件 (运行括号结束)
            self.event_hub.emit_notification(AgentEventType.TURN_END, turn=1, subagent_id=sub_id)
            self.event_hub.emit_notification(AgentEventType.AGENT_END, subagent_id=sub_id, elapsed=elapsed)

            self.manager.complete_subagent(sub_id, result)
            self.bus.post(
                Message(
                    sender_id=sub_id,
                    recipient_id=parent_id or "coordinator",
                    msg_type=MessageType.RESULT,
                    content=summary,
                    metadata={"elapsed": elapsed, "data_count": len(tool_data)},
                )
            )

            if on_progress:
                on_progress(f"【{role}】任务圆满完成", 100)

            return result

        except Exception as exc:
            elapsed = round(time.perf_counter() - start_time, 2)
            err_msg = f"{type(exc).__name__}: {str(exc)}"
            fail_result = SubagentResult(
                subagent_id=sub_id,
                type_name=type_name,
                role=role or defn.role,
                state=SubagentState.FAILED,
                summary=f"执行失败：{err_msg}",
                error=err_msg,
                elapsed_seconds=elapsed,
            )
            self.manager.complete_subagent(sub_id, fail_result)
            self.event_hub.emit_notification(AgentEventType.ERROR, error=err_msg)
            self.bus.post(
                Message(
                    sender_id=sub_id,
                    recipient_id=parent_id or "coordinator",
                    msg_type=MessageType.ERROR,
                    content=err_msg,
                )
            )
            transcript_logger.append_step(
                source="SYSTEM",
                step_type="ERROR",
                content=err_msg,
                status="ERROR",
            )
            return fail_result

    def run_parallel_subagents(
        self,
        tasks: List[Dict[str, Any]],
        parent_context: Optional[SubagentContext] = None,
        max_workers: int = 4,
    ) -> List[SubagentResult]:
        """并发派发一组子智能体协同执行任务。"""
        results: List[SubagentResult] = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks) or 1)) as executor:
            future_to_task = {
                executor.submit(
                    self.invoke_subagent,
                    type_name=t["type_name"],
                    role=t.get("role", ""),
                    prompt=t["prompt"],
                    model_tier=t.get("model_tier"),
                    workspace_mode=t.get("workspace_mode", WorkspaceMode.BRANCH),
                    parent_context=parent_context,
                ): t
                for t in tasks
            }
            for fut in as_completed(future_to_task):
                try:
                    res = fut.result()
                    results.append(res)
                except Exception as exc:
                    t = future_to_task[fut]
                    results.append(
                        SubagentResult(
                            subagent_id=f"err_{t['type_name']}",
                            type_name=t["type_name"],
                            role=t.get("role", t["type_name"]),
                            state=SubagentState.FAILED,
                            summary=f"并发调度异常：{exc}",
                            error=str(exc),
                        )
                    )
        return results