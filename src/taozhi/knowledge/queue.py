"""Kafka 任务事件层：Outbox 发布与消费认领。

角色约定：
- 发布：create_job 同事务写 task_outbox，relay 线程异步投递到 Kafka 后删除 outbox 行；
- 消息体仅携带 job_id，认领复用持久化仓储中的状态机抢占方法，重复投递天然幂等；
- 停机信号由 Worker 进程显式注入，统一实现优雅退出。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any, Callable

from taozhi.config import load_config
from taozhi.infrastructure.persistence import job_repo
from taozhi.knowledge.db import database_connection, fetch_all

_LOG = logging.getLogger(__name__)
_GROUP = "ecom-workers"


def ensure_topic() -> None:
    """确保任务主题存在且分区数满足当前部署配置。"""
    cfg = load_config()
    from kafka.admin import KafkaAdminClient, NewPartitions, NewTopic
    from kafka.errors import InvalidPartitionsError, TopicAlreadyExistsError, UnknownTopicOrPartitionError

    admin = KafkaAdminClient(bootstrap_servers=cfg.kafka_brokers.split(","), request_timeout_ms=8000)
    try:
        try:
            admin.create_topics([NewTopic(name=cfg.kafka_topic, num_partitions=cfg.kafka_partitions, replication_factor=1)])
            return
        except TopicAlreadyExistsError:
            pass
        try:
            admin.create_partitions({cfg.kafka_topic: NewPartitions(total_count=cfg.kafka_partitions)})
        except (InvalidPartitionsError, UnknownTopicOrPartitionError):
            pass
    finally:
        try:
            admin.close()
        except Exception as exc:
            _LOG.debug("关闭 KafkaAdminClient 异常: %s", exc)


def publish_pending(producer: Any = None) -> int:
    """将 Outbox 待发任务投递到 Kafka 并在同事务内清理已发送行。

    Args:
        producer: 可复用的 KafkaProducer 实例；未传入时按单次调用临时创建并在结束后关闭。

    Returns:
        本次成功投递并删除的 Outbox 记录数量。
    """
    cfg = load_config()
    with database_connection() as c:
        rows = fetch_all(c, "SELECT id,job_id FROM task_outbox ORDER BY id LIMIT 200 FOR UPDATE SKIP LOCKED")
        if not rows:
            return 0
        owns_producer = producer is None
        client = producer
        if client is None:
            from kafka import KafkaProducer

            client = KafkaProducer(bootstrap_servers=cfg.kafka_brokers.split(","), acks="all", retries=5)
        try:
            for r in rows:
                client.send(
                    cfg.kafka_topic,
                    value=json.dumps({"job_id": r["job_id"]}).encode(),
                    key=r["job_id"].encode(),
                )
            client.flush(timeout=10)
        finally:
            if owns_producer:
                try:
                    client.close(timeout=5)
                except Exception as exc:
                    _LOG.debug("关闭临时 KafkaProducer 异常: %s", exc)
        c.execute("DELETE FROM task_outbox WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    return len(rows)


def relay_loop(stop_event: threading.Event | None = None) -> None:
    """持续轮询 Outbox 并将任务标识投递至 Kafka（复用长连接 Producer）。

    Args:
        stop_event: 控制循环优雅退出的线程事件信号。
    """
    stop = stop_event or threading.Event()
    topic_ready = False
    producer: Any = None
    while not stop.is_set():
        if not topic_ready:
            try:
                ensure_topic()
                topic_ready = True
            except Exception as exc:
                _LOG.warning("Kafka 主题初始化重试 (%s)", type(exc).__name__)
                stop.wait(2)
                continue
        try:
            if producer is None:
                from kafka import KafkaProducer

                cfg = load_config()
                producer = KafkaProducer(bootstrap_servers=cfg.kafka_brokers.split(","), acks="all", retries=5)
            publish_pending(producer=producer)
        except Exception as exc:
            _LOG.warning("Kafka 投递重试 (%s): %s", type(exc).__name__, exc)
            if producer is not None:
                try:
                    producer.close(timeout=2)
                except Exception as close_exc:
                    _LOG.debug("关闭异常 KafkaProducer: %s", close_exc)
                producer = None
            stop.wait(2)
        else:
            stop.wait(0.5)
    if producer is not None:
        try:
            producer.close(timeout=5)
        except Exception as exc:
            _LOG.debug("停机关闭 KafkaProducer 异常: %s", exc)


def claim_job_by_id(job_id: str, worker_id: str | None = None) -> dict[str, Any] | None:
    """按任务编号幂等抢占排队或租约过期任务。"""
    wid = worker_id or f"worker_{secrets.token_hex(8)}"
    return job_repo.claim_job_by_id(job_id, wid)


def consume_forever(
    run_job: Callable[[dict[str, Any]], None],
    sweep: Callable[[], None],
    *,
    worker_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """阻塞消费 Kafka 任务消息并调度执行，空闲周期执行数据库对账清扫。

    Args:
        run_job: 单条任务执行回调函数。
        sweep: 空窗对账与超时清理回调函数。
        worker_id: 当前 Worker 节点唯一标识。
        stop_event: 优雅停机控制信号。
    """
    from kafka import KafkaConsumer

    cfg = load_config()
    wid = worker_id or f"worker_{secrets.token_hex(8)}"
    stop = stop_event or threading.Event()
    while not stop.is_set():
        try:
            consumer = KafkaConsumer(
                cfg.kafka_topic,
                bootstrap_servers=cfg.kafka_brokers.split(","),
                group_id=_GROUP,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                consumer_timeout_ms=3000,
                value_deserializer=lambda b: json.loads(b.decode()),
            )
        except Exception as exc:
            _LOG.warning("Kafka 消费重连 (%s)", type(exc).__name__)
            stop.wait(3)
            continue
        try:
            it = iter(consumer)
            last_sweep = 0.0
            while not stop.is_set():
                try:
                    msg = next(it)
                except StopIteration:
                    if time.time() - last_sweep >= 5:
                        sweep()
                        last_sweep = time.time()
                    continue
                job_id = (msg.value or {}).get("job_id", "")
                job = claim_job_by_id(job_id, worker_id=wid) if job_id else None
                # 抢占状态已持久化落库（含租约与 sweep 兜底），立即提交偏移量避免长耗时任务阻塞重平衡
                consumer.commit()
                if job:
                    run_job(job)
                if time.time() - last_sweep >= 5:
                    sweep()
                    last_sweep = time.time()
        except Exception as exc:
            _LOG.warning("Kafka 消费循环重连 (%s): %s", type(exc).__name__, exc)
            stop.wait(3)
        finally:
            try:
                consumer.close()
            except Exception as exc:
                _LOG.debug("关闭 KafkaConsumer 异常: %s", exc)
