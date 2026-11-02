"""子智能体生命周期管理器（Subagent Lifecycle Manager）。

自研生命周期治理架构：
1. 实现完整的生命周期管理契约（list, status, kill, kill_all, send_input）；
2. 支持树状级联销毁（Killing a subagent also kills its descendants）；
3. 自动回收分支工作区（Branch Workspace），完好保留产物与日志；
4. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from .bus import MessageBus
from .context import SubagentContext
from .protocol import Message, MessageType, SubagentResult, SubagentState


class ManagedSubagent:
    """包装受纳管的子智能体运行时实例。"""

    def __init__(
        self,
        conversation_id: str,
        type_name: str,
        role: str,
        context: SubagentContext,
        parent_id: Optional[str] = None,
    ) -> None:
        """初始化纳管实例。

        Args:
            conversation_id: 唯一会话 ID。
            type_name: 智能体类型名。
            role: 执行角色。
            context: 隔离执行上下文。
            parent_id: 父级会话 ID。
        """
        self.conversation_id = conversation_id
        self.type_name = type_name
        self.role = role
        self.context = context
        self.parent_id = parent_id
        self.state = SubagentState.RUNNING
        self.start_time = time.perf_counter()
        self.end_time: Optional[float] = None
        self.result: Optional[SubagentResult] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.logs: List[str] = []

    def log(self, line: str) -> None:
        """记录一行运行时日志。

        Args:
            line: 日志文本。
        """
        self.logs.append(line)

    @property
    def elapsed_seconds(self) -> float:
        """获取当前已运行耗时（秒）。

        Returns:
            float: 耗时秒数。
        """
        if self.end_time is not None:
            return round(self.end_time - self.start_time, 2)
        return round(time.perf_counter() - self.start_time, 2)


class SubagentManager:
    """管理全局子智能体实例生命周期、状态查询与级联销毁。"""

    def __init__(self, bus: Optional[MessageBus] = None) -> None:
        """初始化管理器。

        Args:
            bus: 消息总线。
        """
        self.bus = bus or MessageBus()
        self._agents: Dict[str, ManagedSubagent] = {}
        self._lock = threading.Lock()

    def register_subagent(
        self,
        context: SubagentContext,
        type_name: str,
        role: str,
        parent_id: Optional[str] = None,
    ) -> ManagedSubagent:
        """向管理器注册一个新启动的子智能体。

        Args:
            context: 执行上下文。
            type_name: 类型名称。
            role: 执行角色。
            parent_id: 父级会话 ID。

        Returns:
            ManagedSubagent: 受管对象。
        """
        with self._lock:
            agent = ManagedSubagent(
                conversation_id=context.conversation_id,
                type_name=type_name,
                role=role,
                context=context,
                parent_id=parent_id,
            )
            self._agents[agent.conversation_id] = agent
            return agent

    def complete_subagent(
        self,
        conversation_id: str,
        result: SubagentResult,
    ) -> None:
        """标记子智能体执行完成。

        Args:
            conversation_id: 会话 ID。
            result: 执行产物结果。
        """
        with self._lock:
            agent = self._agents.get(conversation_id)
            if agent:
                agent.end_time = time.perf_counter()
                agent.state = result.state
                agent.result = result

    def list_subagents(self) -> List[Dict[str, Any]]:
        """列出所有已注册的智能体实时快照。

        Returns:
            List[Dict[str, Any]]: 智能体状态快照字典列表。
        """
        with self._lock:
            snapshots = []
            for a in self._agents.values():
                snapshots.append({
                    "conversationId": a.conversation_id,
                    "typeName": a.type_name,
                    "role": a.role,
                    "state": a.state.value,
                    "parentId": a.parent_id,
                    "elapsedSeconds": a.elapsed_seconds,
                    "artifactCount": len(a.result.artifacts) if a.result else 0,
                })
            return snapshots

    def get_status(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取指定子智能体的详细状态及最新运行日志。

        Args:
            conversation_id: 会话 ID。

        Returns:
            Optional[Dict[str, Any]]: 详细字典，不存在则返回 None。
        """
        with self._lock:
            a = self._agents.get(conversation_id)
            if not a:
                return None
            return {
                "conversationId": a.conversation_id,
                "typeName": a.type_name,
                "role": a.role,
                "state": a.state.value,
                "elapsedSeconds": a.elapsed_seconds,
                "recentLogs": a.logs[-20:],
                "scratchDir": str(a.context.scratch_dir),
                "artifactsDir": str(a.context.artifacts_dir),
            }

    def kill_subagent(self, conversation_id: str) -> bool:
        """销毁指定子智能体并递归级联销毁其全部派生后代。

        Args:
            conversation_id: 待终止的目标会话 ID。

        Returns:
            bool: 是否成功执行终止。
        """
        with self._lock:
            target = self._agents.get(conversation_id)
            if not target:
                return False

            # 找到全部子孙节点
            to_kill = [conversation_id]
            idx = 0
            while idx < len(to_kill):
                curr_id = to_kill[idx]
                idx += 1
                for sub_id, sub_agent in self._agents.items():
                    if sub_agent.parent_id == curr_id and sub_id not in to_kill:
                        to_kill.append(sub_id)

            # 逆序逐个销毁与资源回收
            for cid in reversed(to_kill):
                agent = self._agents.get(cid)
                if not agent:
                    continue
                agent.stop_event.set()
                agent.state = SubagentState.FAILED
                agent.end_time = time.perf_counter()
                agent.log("Subagent cancelled by manager")

                # 发布取消事件
                self.bus.post(
                    Message(
                        sender_id="manager",
                        recipient_id=cid,
                        msg_type=MessageType.ERROR,
                        content=f"Subagent {cid} was killed by manager.",
                    )
                )

                # 回收分支临时目录（保留 artifacts 与 logs）
                if agent.context.workspace_dir and agent.context.workspace_dir.exists():
                    try:
                        shutil.rmtree(agent.context.workspace_dir, ignore_errors=True)
                    except Exception:
                        pass

            return True

    def kill_all(self) -> int:
        """终止所有处于运行中的子智能体。

        Returns:
            int: 终止的数量。
        """
        active_ids = [
            cid for cid, a in self._agents.items()
            if a.state == SubagentState.RUNNING
        ]
        count = 0
        for cid in active_ids:
            if self.kill_subagent(cid):
                count += 1
        return count

    def send_input(self, conversation_id: str, input_text: str) -> bool:
        """向正在执行的智能体投递干预输入。

        Args:
            conversation_id: 目标智能体会话 ID。
            input_text: 交互指令文本。

        Returns:
            bool: 投递是否成功。
        """
        with self._lock:
            agent = self._agents.get(conversation_id)
            if not agent or agent.state != SubagentState.RUNNING:
                return False

            agent.log(f"Received interactive input: {input_text}")
            self.bus.post(
                Message(
                    sender_id="coordinator",
                    recipient_id=conversation_id,
                    msg_type=MessageType.INTER_AGENT,
                    content=input_text,
                )
            )
            return True
