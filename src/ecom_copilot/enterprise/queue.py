"""Kafka 任务事件层：outbox 发布 + 消费认领。

角色约定：
- 发布：create_job 同事务写 task_outbox，relay 线程异步投递到 Kafka 后删除 outbox 行；
- 消息体只带 job_id，认领走数据库 UPDATE（状态机天然幂等），重复消息安全；
- 数据库轮询保留为对账兜底，Kafka 不可达时任务照常排队执行。
"""
from __future__ import annotations
import json,secrets,threading,time
from .config import load_config
from .db import database_connection,fetch_all,fetch_one

STOP=threading.Event()
_group="ecom-workers"
_worker_id="worker_"+secrets.token_hex(8)

def ensure_topic() -> None:
    """确保任务主题存在且分区数达标。

    自动创建的主题只吃 broker 的 num.partitions（常见为 1），并行消费失效；
    这里显式建主题并按 ECOM_KAFKA_PARTITIONS 扩展分区，已达标时保持不动。
    """
    cfg=load_config()
    from kafka.admin import KafkaAdminClient,NewTopic,NewPartitions
    from kafka.errors import TopicAlreadyExistsError,InvalidPartitionsError,UnknownTopicOrPartitionError
    admin=KafkaAdminClient(bootstrap_servers=cfg.kafka_brokers.split(","),request_timeout_ms=8000)
    try:
        try:
            admin.create_topics([NewTopic(name=cfg.kafka_topic,num_partitions=cfg.kafka_partitions,replication_factor=1)])
            return
        except TopicAlreadyExistsError:
            pass
        try:
            admin.create_partitions({cfg.kafka_topic:NewPartitions(total_count=cfg.kafka_partitions)})
        except (InvalidPartitionsError,UnknownTopicOrPartitionError):
            pass
    finally:
        try:admin.close()
        except Exception:pass

def publish_pending():
    """把 outbox 中的待发任务投递到 Kafka；成功后在同一事务内删除对应行。

    用 FOR UPDATE SKIP LOCKED 抢占待发行，多个 worker 的 relay 线程不会重复投递同一批；
    行锁覆盖投递到删除的全过程；若投递中断，事务回滚后下游仍按 job_id 幂等认领兜底。
    """
    cfg=load_config()
    with database_connection() as c:
        rows=fetch_all(c,"SELECT id,job_id FROM task_outbox ORDER BY id LIMIT 200 FOR UPDATE SKIP LOCKED")
        if not rows:return 0
        from kafka import KafkaProducer
        producer=KafkaProducer(bootstrap_servers=cfg.kafka_brokers.split(","),acks="all",retries=5)
        try:
            for r in rows:
                producer.send(cfg.kafka_topic,value=json.dumps({"job_id":r["job_id"]}).encode(),key=r["job_id"].encode())
            producer.flush(timeout=10)
        finally:
            try:producer.close(timeout=5)
            except Exception:pass
        c.execute("DELETE FROM task_outbox WHERE id = ANY(%s)",([r["id"] for r in rows],))
    return len(rows)

def relay_loop():
    topic_ready=False
    while not STOP.is_set():
        if not topic_ready:
            try:ensure_topic();topic_ready=True
            except Exception as exc:
                print("Kafka 主题初始化重试",type(exc).__name__,flush=True);STOP.wait(2);continue
        try:publish_pending()
        except Exception as exc:
            print("Kafka 投递重试",type(exc).__name__,flush=True);STOP.wait(2)
        else:STOP.wait(0.5)

def claim_job_by_id(job_id):
    """按消息认领任务；状态机保证同一任务只被一个执行体抢到。"""
    with database_connection() as c:
        row=fetch_one(c,"SELECT id FROM jobs WHERE id=%s AND NOT cancel_requested AND attempts<%s AND (state='queued' OR (state='running' AND lease_until<now())) FOR UPDATE SKIP LOCKED",
            (job_id,load_config().max_attempts))
        if not row:return None
        return fetch_one(c,"UPDATE jobs SET state='running',run_version=run_version+1,attempts=attempts+1,worker_id=%s,lease_until=now()+(%s*interval '1 second'),started_at=COALESCE(started_at,now()),error='' WHERE id=%s RETURNING *",
            (_worker_id,load_config().lease_seconds,row["id"]))

def consume_forever(run_job,sweep):
    """阻塞消费：逐条认领并同步执行，提交位点后继续；空闲期做对账清扫。

    run_job(job)：执行已认领的任务；sweep()：对账兜底（自认领+执行+心跳）。
    """
    from kafka import KafkaConsumer
    cfg=load_config()
    while not STOP.is_set():
        try:
            consumer=KafkaConsumer(cfg.kafka_topic,bootstrap_servers=cfg.kafka_brokers.split(","),group_id=_group,
                auto_offset_reset="earliest",enable_auto_commit=False,consumer_timeout_ms=3000,
                value_deserializer=lambda b:json.loads(b.decode()))
        except Exception as exc:
            print("Kafka 消费重连",type(exc).__name__,flush=True);STOP.wait(3);continue
        try:
            it=iter(consumer);last_sweep=0.0
            while not STOP.is_set():
                try:msg=next(it)
                except StopIteration:
                    if time.time()-last_sweep>=5:sweep();last_sweep=time.time()
                    continue
                job_id=(msg.value or {}).get("job_id","")
                job=claim_job_by_id(job_id) if job_id else None
                if job:run_job(job)
                consumer.commit()
                if time.time()-last_sweep>=5:sweep();last_sweep=time.time()
        except Exception as exc:
            print("Kafka 消费重连",type(exc).__name__,flush=True);STOP.wait(3)
        finally:
            try:consumer.close()
            except Exception:pass
