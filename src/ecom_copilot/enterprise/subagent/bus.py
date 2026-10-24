"""多智能体反应式事件消息总线（Reactive Message Bus）。

自研反应式架构设计：
1. 彻底弃用 While 轮询（No Polling），采用条件变量与事件唤醒；
2. 事件驱动（Event-Driven）精准投递与唤醒通知；
3. 父子智能体解耦，支持广播 (broadcast) 与点对点信道 (peer-to-peer)；
4. 注释规范遵循 Google Python Style Guide。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .protocol import Message, MessageType


class MessageBus:
    """线程安全的智能体反应式通信总线。

    提供消息路由、观察者订阅与非阻塞/同步阻塞条件唤醒能力。
    """

    def __init__(self) -> None:
        """初始化消息总线。"""
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._history: List[Message] = []
        self._mailboxes: Dict[str, List[Message]] = defaultdict(list)
        self._subscribers: Dict[str, List[Callable[[Message], None]]] = defaultdict(list)
        self._broadcast_subscribers: List[Callable[[Message], None]] = []

    def post(self, message: Message) -> None:
        """发布并投递一条消息。

        自动将消息推入目标邮箱，并触发订阅者回调，最后通知等待在总线上的线程。

        Args:
            message (Message): 待发布的智能体消息对象。
        """
        callbacks: List[Callable[[Message], None]] = []
        with self._lock:
            self._history.append(message)
            recipient = message.recipient_id

            if recipient == "broadcast":
                callbacks.extend(self._broadcast_subscribers)
                for subs in self._subscribers.values():
                    callbacks.extend(subs)
            else:
                self._mailboxes[recipient].append(message)
                callbacks.extend(self._subscribers.get(recipient, []))
                callbacks.extend(self._broadcast_subscribers)

            # 唤醒所有挂起等待事件的观察线程（Reactive Wakeup）
            self._condition.notify_all()

        # 在锁外执行回调，防止死锁
        for cb in callbacks:
            try:
                cb(message)
            except Exception:
                pass

    def subscribe(
        self,
        agent_id: str,
        callback: Callable[[Message], None],
    ) -> None:
        """为特定智能体订阅其专属投递信道。

        Args:
            agent_id (str): 目标智能体 ID，为 '*' 时订阅全量广播。
            callback (Callable[[Message], None]): 消息到达时触发的同步回调。
        """
        with self._lock:
            if agent_id == "*":
                self._broadcast_subscribers.append(callback)
            else:
                self._subscribers[agent_id].append(callback)

    def get_messages(
        self,
        agent_id: str,
        msg_type: Optional[MessageType] = None,
        clear: bool = False,
    ) -> List[Message]:
        """获取目标智能体收件箱中的全部未读消息。

        Args:
            agent_id: 智能体 ID。
            msg_type: 可选的消息类型过滤。
            clear: 是否在读取后清空已读。

        Returns:
            List[Message]: 符合条件的消息列表。
        """
        with self._lock:
            box = self._mailboxes.get(agent_id, [])
            if msg_type is not None:
                matched = [m for m in box if m.msg_type == msg_type]
                if clear:
                    self._mailboxes[agent_id] = [m for m in box if m.msg_type != msg_type]
                return matched
            out = list(box)
            if clear:
                self._mailboxes[agent_id] = []
            return out

    def wait_for_message(
        self,
        recipient_id: str,
        condition: Optional[Callable[[Message], bool]] = None,
        timeout: float = 30.0,
    ) -> Optional[Message]:
        """反应式挂起并等待目标消息到达（取代轮询）。

        当前线程在条件变量上休眠，一旦有新消息到达即被唤醒并判断条件，
        直至条件满足或超时，CPU 占用为零。

        Args:
            recipient_id: 期望接收消息的智能体 ID。
            condition: 判别函数，返回 True 表示目标消息已命中。
            timeout: 最长等待超时时间（秒）。

        Returns:
            Optional[Message]: 命中的消息对象；超时则返回 None。
        """
        deadline = time.time() + timeout
        with self._condition:
            while True:
                # 检查当前邮箱中已有消息
                for idx, msg in enumerate(self._mailboxes.get(recipient_id, [])):
                    if condition is None or condition(msg):
                        return self._mailboxes[recipient_id].pop(idx)

                remaining = deadline - time.time()
                if remaining <= 0:
                    return None

                # 挂起休眠等待新消息通知唤醒
                self._condition.wait(timeout=remaining)
