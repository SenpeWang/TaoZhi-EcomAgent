"""子智能体执行引擎（Subagent Runner）。

自研执行引擎核心架构：
1. invoke_subagent 调度隔离执行并接入 Transcript 双轨日志；
2. 接入 SubagentManager 纳管生命周期与级联销毁；
3. 反应式总线发布进度与终态结果，支持交互干预与条件唤醒；
4. 大容量原始数据自动落盘 Scratch，上下文仅保留高密度 Summary；
5. 自动集成 RulesEngine 规范、SkillRegistry 技能与 ContextCompactor 压实；
6. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from ...llm.client import LLMClient, ModelTier
from .bus import MessageBus
from .commands import SlashCommandRegistry
from .context import ArtifactMetadata, SubagentContext
from .manager import SubagentManager
from .memory.compactor import ContextCompactor
from .memory.store import LongTermMemoryStore
from .memory.transcript import TranscriptLogger
from .protocol import (
    Message,
    MessageType,
    SubagentDefinition,
    SubagentResult,
    SubagentState,
    WorkspaceMode,
)
from .registry import default_registry
from .rules import RulesEngine
from .scheduler import AgentScheduler
from .skills import SkillRegistry
from .tools import extract_search_keyword


class SubagentRunner:
    """负责子智能体的生命周期管理、任务派发与并发执行调度。"""

    def __init__(
        self,
        bus: Optional[MessageBus] = None,
        llm_client: Optional[LLMClient] = None,
        memory_store: Optional[LongTermMemoryStore] = None,
    ) -> None:
        """初始化执行引擎。

        Args:
            bus: 消息总线实例，默认新建。
            llm_client: LLM 客户端实例，默认新建。
            memory_store: 长效持久化记忆库，默认新建。
        """
        self.bus = bus or MessageBus()
        self.llm = llm_client or LLMClient()
        self.registry = default_registry
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
        """派发并执行单个子智能体任务。

        Args:
            type_name: 智能体类型名（统一采用 _agent 后缀）。
            role: 本次执行的具体角色说明。
            prompt: 具体且可落地的任务指令（明确预期输出格式）。
            model_tier: 可选覆盖默认模型档次 (fast / strong / vision)。
            workspace_mode: 隔离模式。
            parent_context: 父级上下文。
            custom_tools: 额外附加的临时工具。
            on_progress: 实时进度通知回调。

        Returns:
            SubagentResult: 结构化运行产物。
        """
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

        # 1. 工具调用阶段
        tools_to_run = list(defn.tools) + (custom_tools or [])
        tool_data: List[Dict[str, Any]] = []
        scratch_files: List[str] = []

        try:
            for tool in tools_to_run:
                if managed_agent.stop_event.is_set():
                    raise RuntimeError("Subagent execution cancelled")

                fn_name = tool.__name__
                if "search" in fn_name or "wholesale" in fn_name:
                    search_query = extract_search_keyword(prompt) or prompt
                    transcript_logger.append_step(
                        source="MODEL",
                        step_type="TOOL_CALL",
                        content=f"Calling tool {fn_name}",
                        tool_calls=[{"name": fn_name, "parameters": {"query": search_query, "limit": 15}}],
                    )

                    res = tool(search_query, limit=15)
                    if isinstance(res, list):
                        tool_data.extend(res)
                        # 将海量原始数据写入 scratch 目录，防止撑爆父级上下文
                        raw_file = ctx.write_scratch(f"raw_{fn_name}.json", json.dumps(res, ensure_ascii=False, indent=2))
                        scratch_files.append(str(raw_file))

                        transcript_logger.append_step(
                            source="SYSTEM",
                            step_type="TOOL_RESULT",
                            content=f"Tool {fn_name} returned {len(res)} items, saved to scratch.",
                            metadata={"file": str(raw_file), "count": len(res)},
                        )

            if on_progress:
                on_progress(f"【{role}】已采集数据并深入研判中", 60)

            # 2. LLM 推理归纳阶段
            data_context = ""
            if tool_data:
                compressed_preview = "\n".join(
                    f"{i+1}. {d.get('title', '')} | 价格: {d.get('price', '')} | 来源: {d.get('shop', d.get('source', ''))}"
                    for i, d in enumerate(tool_data[:15])
                )
                data_context = f"\n\n【已采集到的实时数据预览（共 {len(tool_data)} 条）】：\n{compressed_preview}"

            rules_block = self.rules_engine.build_rules_prompt_block()
            rule_addon = f"\n\n{rules_block}" if rules_block else ""

            system_inst = (
                f"{defn.system_prompt}\n\n"
                "【严格原则】：\n"
                "1. 结论必须百分之百基于采集到的事实数据，严禁虚构造假数字；\n"
                "2. 提炼出 3-5 条关键业务要点（价格带、爆款卖点、供应商特征、潜在风险）；\n"
                "3. 输出中文结构化总结。"
                f"{rule_addon}"
            )

            user_query = f"任务指令：{prompt}{data_context}"
            summary = self.llm.chat(
                user_query,
                tier=tier,
                system=system_inst,
                max_tokens=2048,
            )

            transcript_logger.append_step(
                source="MODEL",
                step_type="PLANNER_RESPONSE",
                content=summary,
                metadata={"tier": tier.value if hasattr(tier, "value") else str(tier)},
            )

            # 3. 产物交付与持久化（附带标准元数据）
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

            # 标记纳管完成并通知总线
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
        """并发派发一组子智能体协同执行任务。

        每个任务字典形如：
        {"type_name": str, "role": str, "prompt": str, "model_tier": str}

        Args:
            tasks: 任务列表。
            parent_context: 父级协调上下文。
            max_workers: 最大并发线程数。

        Returns:
            List[SubagentResult]: 智能体产物结果列表。
        """
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
