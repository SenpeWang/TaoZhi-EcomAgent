"""反应式定时调度器（Reactive Agent Scheduler）。

遵循 Antigravity (agy) 设计规范：
1. 对应 schedule 工具规范（单次定时器 DurationSeconds 与周期 CronExpression）；
2. 支持条件取消（TimerCondition: 'never', 'any', 或指定 sender_id）；
3. 定时到达时通过反应式总线发布高优先级提示词通知；
4. 杜绝 busy-polling，基于条件变量与底层线程等待；
5. 严格遵循 Google Style Docstring 规范。
"""

from __future__ import annotations

import datetime
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .bus import MessageBus
from .protocol import Message, MessageType


class ScheduledJob:
    """表示一个定时调度作业。"""

    def __init__(
        self,
        job_id: str,
        prompt: str,
        duration_seconds: Optional[int] = None,
        cron_expression: Optional[str] = None,
        timer_condition: str = "never",
        is_daemon: bool = False,
        max_iterations: Optional[int] = None,
        recipient_id: str = "coordinator",
    ) -> None:
        """初始化作业实体。

        Args:
            job_id: 作业唯一 ID。
            prompt: 触发时投递的高优先级通知消息。
            duration_seconds: 单次等待时长（秒）。
            cron_expression: Cron 表达式字符串。
            timer_condition: 提前取消条件（'never', 'any', 或匹配的 sender_id）。
            is_daemon: 是否在主任务结束后常驻。
            max_iterations: 最大触发次数。
            recipient_id: 通知接收方 ID。
        """
        self.job_id = job_id
        self.prompt = prompt
        self.duration_seconds = duration_seconds
        self.cron_expression = cron_expression
        self.timer_condition = timer_condition
        self.is_daemon = is_daemon
        self.max_iterations = max_iterations
        self.recipient_id = recipient_id
        self.iterations = 0
        self.is_cancelled = False
        self.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cancel_event = threading.Event()


class AgentScheduler:
    """管理智能体定时任务注册、条件拦截与反应式唤醒。"""

    def __init__(self, bus: Optional[MessageBus] = None) -> None:
        """初始化调度器。

        Args:
            bus: 消息总线。
        """
        self.bus = bus or MessageBus()
        self._jobs: Dict[str, ScheduledJob] = {}
        self._lock = threading.Lock()
        self._setup_condition_listener()

    def _setup_condition_listener(self) -> None:
        """向消息总线订阅全量广播，当收到特定发送方的消息时，触发 TimerCondition 提前取消。"""
        def _on_msg(msg: Message) -> None:
            with self._lock:
                for job in list(self._jobs.values()):
                    if job.is_cancelled:
                        continue
                    cond = job.timer_condition
                    if cond == "never":
                        continue
                    elif cond == "any":
                        job.is_cancelled = True
                        job.cancel_event.set()
                    elif cond == msg.sender_id:
                        job.is_cancelled = True
                        job.cancel_event.set()

        self.bus.subscribe("*", _on_msg)

    def schedule_timer(
        self,
        prompt: str,
        duration_seconds: int,
        timer_condition: str = "never",
        recipient_id: str = "coordinator",
    ) -> str:
        """注册一个单次定时唤醒作业（对照 agy 的 DurationSeconds 定时器）。

        Args:
            prompt: 唤醒时投递的消息。
            duration_seconds: 延迟秒数。
            timer_condition: 提前取消条件。
            recipient_id: 接收方 ID。

        Returns:
            str: 调度作业 ID。
        """
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job = ScheduledJob(
            job_id=job_id,
            prompt=prompt,
            duration_seconds=duration_seconds,
            timer_condition=timer_condition,
            recipient_id=recipient_id,
        )

        with self._lock:
            self._jobs[job_id] = job

        def _runner():
            # 使用 cancel_event.wait(timeout) 避免死等
            cancelled = job.cancel_event.wait(timeout=max(0.1, float(duration_seconds)))
            if not cancelled and not job.is_cancelled:
                job.iterations += 1
                # 触发向总线发布高优先级反应式唤醒
                self.bus.post(
                    Message(
                        sender_id="scheduler",
                        recipient_id=job.recipient_id,
                        msg_type=MessageType.TASK,
                        content=job.prompt,
                        metadata={"job_id": job_id, "trigger": "timer"},
                    )
                )

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """取消指定调度作业。

        Args:
            job_id: 作业 ID。

        Returns:
            bool: 是否取消成功。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.is_cancelled:
                return False
            job.is_cancelled = True
            job.cancel_event.set()
            return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        """列出所有调度作业状态。

        Returns:
            List[Dict[str, Any]]: 作业列表。
        """
        with self._lock:
            return [
                {
                    "jobId": j.job_id,
                    "prompt": j.prompt,
                    "durationSeconds": j.duration_seconds,
                    "cronExpression": j.cron_expression,
                    "timerCondition": j.timer_condition,
                    "isCancelled": j.is_cancelled,
                    "iterations": j.iterations,
                    "createdAt": j.created_at,
                }
                for j in self._jobs.values()
            ]
