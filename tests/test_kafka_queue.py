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

    # 1. 确保主题存在
    ensure_topic()

    # 2. 从库中拉取真实的合法用户与组织节点外键
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

    # 3. 触发 Outbox 投递至 Kafka
    relayed_count = publish_pending()
    assert relayed_count >= 1

    # 4. 验证 outbox 行已清空（代表成功投递至 Kafka）
    with database_connection() as c:
        row = fetch_one(c, "SELECT id FROM task_outbox WHERE job_id=%s", (test_job_id,))
        assert row is None

    # 5. 测试认领机制：测试库模式下直接验证 claim_job_by_id；生产模式下验证后台 Worker 消费
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

    # 清理测试数据
    with database_connection() as c:
        c.execute("DELETE FROM model_calls WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM task_events WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM task_outbox WHERE job_id=%s", (test_job_id,))
        c.execute("DELETE FROM jobs WHERE id=%s", (test_job_id,))