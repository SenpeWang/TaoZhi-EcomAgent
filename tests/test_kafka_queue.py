"""Kafka 任务事件层与 Outbox Relay 机制集成验证。"""
import time
import pytest
from taozhi.config import load_config
from taozhi.knowledge.db import database_connection, fetch_one
from taozhi.knowledge.queue import ensure_topic, publish_pending, claim_job_by_id


def test_kafka_topic_and_outbox_relay():
    """测试 Kafka 主题初始化与 Transactional Outbox 投递闭环。"""
    cfg = load_config()
    try:
        from kafka import KafkaConsumer
        consumer = KafkaConsumer(bootstrap_servers=cfg.kafka_brokers.split(","), request_timeout_ms=3000)
        consumer.close()
    except Exception:
        pytest.skip("Kafka Broker 未启动或不可达，跳过集成测试")

    ensure_topic()

    test_job_id = "job_test_kafka_relay_worker"
    with database_connection() as c:
        user = fetch_one(c, "SELECT id, tenant_id FROM users LIMIT 1")
        if not user:
            pytest.skip("数据库无用户数据，跳过")
        node = fetch_one(c, "SELECT id FROM org_nodes WHERE tenant_id=%s LIMIT 1", (user["tenant_id"],))
        node_id = node["id"] if node else "node_root"

        c.execute(
            """INSERT INTO jobs (id, tenant_id, owner_id, owner_version, node_id, kind, state, run_version, question, fingerprint)
               VALUES (%s, %s, %s, 1, %s, 'ask', 'queued', 1, 'Kafka 测试', 'fp_kafka')
               ON CONFLICT (id) DO UPDATE SET state='queued', attempts=0, lease_until=NULL""",
            (test_job_id, user["tenant_id"], user["id"], node_id)
        )
        c.execute("DELETE FROM task_outbox WHERE job_id=%s", (test_job_id,))
        c.execute("INSERT INTO task_outbox (job_id) VALUES (%s)", (test_job_id,))

    relayed_count = publish_pending()
    assert relayed_count >= 1

    with database_connection() as c:
        row = fetch_one(c, "SELECT id FROM task_outbox WHERE job_id=%s", (test_job_id,))
        assert row is None

    if cfg.mode == "test":
        claimed = claim_job_by_id(test_job_id)
        assert claimed is not None
        assert claimed["id"] == test_job_id
        assert claimed["state"] == "running"
    else:
        completed = False
        for _ in range(50):
            time.sleep(0.2)
            with database_connection() as c:
                job = fetch_one(c, "SELECT state, attempts, worker_id FROM jobs WHERE id=%s", (test_job_id,))
                if job and job["state"] in ("completed", "failed"):
                    completed = True
                    break
        assert completed, f"后台 Worker 应从 Kafka 消费并完成任务，实际状态：{job}"

    with database_connection() as c:
        c.execute("DELETE FROM model_calls WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM task_events WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM task_outbox WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM jobs WHERE id=%s", (test_job_id,))

def test_outbox_requires_broker_ack_before_deletion(monkeypatch):
    """只要一条消息投递失败，整批待发任务就继续保留。"""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock
    from taozhi.knowledge import queue

    connection = Mock()
    producer = Mock()
    confirmed, failed = Mock(), Mock()
    failed.get.side_effect = RuntimeError("代理拒绝投递")
    producer.send.side_effect = [confirmed, failed]
    monkeypatch.setattr(queue, "load_config", lambda: SimpleNamespace(kafka_topic="tasks"))
    monkeypatch.setattr(queue, "database_connection", lambda: nullcontext(connection))
    monkeypatch.setattr(queue, "fetch_all", lambda *_: [{"id": 1, "job_id": "a"}, {"id": 2, "job_id": "b"}])

    with pytest.raises(RuntimeError, match="代理拒绝投递"):
        publish_pending(producer)
    connection.execute.assert_not_called()
    producer.send.side_effect = [confirmed, confirmed]
    assert publish_pending(producer) == 2
    connection.execute.assert_called_once()
