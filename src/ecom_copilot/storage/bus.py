"""消息队列抽象：Kafka 异步解耦 + 本地内存队列降级。"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..config import Settings, get_settings


class EventBus:
    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        raise NotImplementedError

    def drain(self, topic: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
        raise NotImplementedError


class LocalEventBus(EventBus):
    """进程内队列 + 后台工作线程，等价于 Kafka 的解耦效果（本地降级）。"""

    name = "local"

    def __init__(self) -> None:
        self._queues: Dict[str, "queue.Queue"] = {}
        self._handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        q = self._queues.setdefault(topic, queue.Queue())
        q.put({"ts": time.time(), **payload})
        self._ensure_worker(topic)

    def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)
        self._ensure_worker(topic)

    def _ensure_worker(self, topic: str) -> None:
        with self._lock:
            if topic in self._threads and self._threads[topic].is_alive():
                return
            thread = threading.Thread(target=self._run, args=(topic,), daemon=True)
            self._threads[topic] = thread
            thread.start()

    def _run(self, topic: str) -> None:
        q = self._queues.setdefault(topic, queue.Queue())
        while True:
            try:
                item = q.get(timeout=1.0)
            except Exception:  # noqa: BLE001
                if not self._handlers.get(topic):
                    return
                continue
            for handler in list(self._handlers.get(topic, [])):
                try:
                    handler(item)
                except Exception:  # noqa: BLE001
                    continue

    def drain(self, topic: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
        q = self._queues.setdefault(topic, queue.Queue())
        items: List[Dict[str, Any]] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                items.append(q.get_nowait())
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
                if items:
                    break
        return items


class KafkaEventBus(EventBus):
    """Kafka 3.7 实现：异步承接解析与向量化任务，解耦耗时链路。"""

    name = "kafka"

    def __init__(self, bootstrap: str, topic_prefix: str = "") -> None:
        from kafka import KafkaConsumer, KafkaProducer  # noqa: PLC0415

        self._bootstrap = bootstrap
        self._prefix = topic_prefix
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"),
        )
        self._KafkaConsumer = KafkaConsumer

    def _topic(self, topic: str) -> str:
        return f"{self._prefix}{topic}" if self._prefix else topic

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        self._producer.send(self._topic(topic), {"ts": time.time(), **payload})
        self._producer.flush(timeout=5)

    def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        consumer = self._KafkaConsumer(
            self._topic(topic),
            bootstrap_servers=self._bootstrap,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            consumer_timeout_ms=5000,
        )
        threading.Thread(
            target=self._consume_loop, args=(consumer, handler), daemon=True
        ).start()

    @staticmethod
    def _consume_loop(consumer, handler):
        for message in consumer:
            try:
                handler(message.value)
            except Exception:  # noqa: BLE001
                continue

    def drain(self, topic: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
        consumer = self._KafkaConsumer(
            self._topic(topic),
            bootstrap_servers=self._bootstrap,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            consumer_timeout_ms=int(timeout * 1000),
        )
        items = [m.value for m in consumer]
        consumer.close()
        return items


_bus: Optional[EventBus] = None


def get_event_bus(settings: Optional[Settings] = None) -> EventBus:
    global _bus
    if _bus is not None:
        return _bus
    cfg = settings or get_settings()
    if cfg.kafka_enabled:
        try:
            _bus = KafkaEventBus(cfg.kafka_bootstrap)
            return _bus
        except Exception:  # noqa: BLE001
            pass
    _bus = LocalEventBus()
    return _bus
