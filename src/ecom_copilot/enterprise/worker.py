from __future__ import annotations
import os,time,secrets,threading,signal,hashlib
from datetime import datetime,timezone
from .config import load_config
from .db import database_connection,fetch_one,fetch_all,as_jsonb
from .policy import (Denied, can_write, get_document, load_principal, sources_valid)
from .services import approval,invalidate_authorization
from .storage import get_blob_store
STOP=threading.Event()
WORKER="worker_"+secrets.token_hex(8)
def claim_next_job():
    with database_connection() as c:
        # Expired executions have fencing numbers; stale workers cannot publish.
        c.execute("UPDATE jobs SET state='failed',error='任务重试次数已达上限',finished_at=now() WHERE state IN ('queued','running') AND attempts>=%s AND (lease_until IS NULL OR lease_until<now())",(load_config().max_attempts,))
        row=fetch_one(c,"SELECT * FROM jobs WHERE NOT cancel_requested AND attempts<%s AND (state='queued' OR (state='running' AND lease_until<now())) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",(load_config().max_attempts,))
        if not row:return None
        return fetch_one(c,"UPDATE jobs SET state='running',run_version=run_version+1,attempts=attempts+1,worker_id=%s,lease_until=now()+(%s*interval '1 second'),started_at=COALESCE(started_at,now()),error='' WHERE id=%s RETURNING *",(WORKER,load_config().lease_seconds,row["id"]))
def renew_job_lease(job,done):
    wid = job.get("worker_id") or WORKER
    while not done.wait(10):
        try:
            with database_connection() as c:
                c.execute("INSERT INTO worker_heartbeats(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET updated_at=now()",(wid,))
                c.execute("UPDATE jobs SET lease_until=now()+(%s*interval '1 second') WHERE id=%s AND run_version=%s AND worker_id=%s AND state='running'",(load_config().lease_seconds,job["id"],job["run_version"],wid))
        except Exception:pass
def _embed_chunks(texts):
    """入库时计算语义向量；嵌入服务不可用时不阻断解析，留待检索侧惰性补算。"""
    if not texts:
        return None
    try:
        from .embedding import available as embed_available, encode_passages
        if not embed_available():
            return None
        return encode_passages(texts)
    except Exception:
        return None
def process_ingestion_job(job):
    from .ingest import parse,split
    from .pipeline import validate_task_execution
    state={"job_id":job["id"],"run_version":job["run_version"]}
    p,_=validate_task_execution(state,"解析资料与登记来源",35)
    with database_connection() as c:
        d=get_document(c,p,job["payload"]["document_id"])
        if not can_write(c,p,d) or d["current_version"]!=job["payload"]["version"]:raise Denied("资料版本或写权限已变化","STALE",409)
        v=fetch_one(c,"SELECT * FROM document_versions WHERE document_id=%s AND version=%s",(d["id"],d["current_version"]))
    if v["blob_name"]:body,pages=parse(get_blob_store().open_blob(p.tenant_id,v["blob_name"]),v["mime_type"])
    else:body,pages=v["body"],[(0,v["body"])]
    chunks=split(pages,d["id"],d["current_version"])
    vectors=_embed_chunks([ch["text"] for ch in chunks])
    validate_task_execution(state,"建立版本索引与发布申请",75)
    with database_connection() as c:
        locked=fetch_one(c,"SELECT * FROM jobs WHERE id=%s FOR UPDATE",(job["id"],))
        d=get_document(c,p,d["id"])
        if locked["run_version"]!=job["run_version"] or locked["state"]!="running" or d["current_version"]!=v["version"]:raise Denied("执行版本已失效","STALE",409)
        c.execute("UPDATE document_versions SET body=%s,content_hash=%s WHERE document_id=%s AND version=%s",(body,hashlib.sha256(body.encode()).hexdigest(),d["id"],v["version"]))
        c.execute("DELETE FROM knowledge_mentions WHERE document_id=%s AND version=%s",(d["id"],v["version"]))
        c.execute("DELETE FROM chunks WHERE document_id=%s AND version=%s",(d["id"],v["version"]))
        for i,ch in enumerate(chunks):
            c.execute("INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
              (ch["id"],p.tenant_id,d["id"],v["version"],ch["ordinal"],ch["page"],ch["text"],
               None if vectors is None else vectors[i].tobytes()))
        from .facts import index_mentions
        index_mentions(c,p.tenant_id,d["id"],v["version"],chunks)
        c.execute("UPDATE documents SET state='review',error='' WHERE id=%s",(d["id"],))
        approval(c,p,"publish",d["id"],dict(expected_acl=d["acl_version"],expected_version=v["version"]),d["node_id"],"boss" if d["level"]==3 or d["scope"]=="company" else "leader")
        c.execute("UPDATE jobs SET state='completed',progress=100,stage='解析完成，等待发布审核',finished_at=now(),lease_until=NULL WHERE id=%s AND run_version=%s",(job["id"],job["run_version"]))
    try:  # 事务提交后同步 ES（尽力而为，失败不阻断入库）
        from . import es_index
        if es_index.available():
            es_index.delete_chunks(d["id"],v["version"])
            with database_connection() as c2:
                rows=fetch_all(c2,"SELECT id,tenant_id,document_id,version,ordinal,page,text,embedding FROM chunks WHERE document_id=%s AND version=%s",(d["id"],v["version"]))
            es_index.index_chunks(rows)
    except Exception as exc:
        print("ES 索引同步失败",type(exc).__name__,flush=True)
def process_question_job(job):
    # 1. 语义缓存层（大促高并发热点削峰拦截，时延 < 15ms，0 LLM 开销）
    from .semantic_cache import get_semantic_cache
    cache = get_semantic_cache()
    cached = cache.get(job["tenant_id"], job["question"], input_level=job["input_level"])
    if cached:
        with database_connection() as c:
            p = load_principal(c, job["owner_id"])
            if sources_valid(c, p, cached.get("sources", [])):
                result = dict(
                    answer=cached["answer"],
                    citations=cached["citations"],
                    specialists=[{"name": "Main 调度 / 语义缓存", "count": 1}],
                    missing=[],
                    hard_block=False,
                    external_blocked=False,
                    model_calls=0,
                    usage=[{"role": "semantic_cache", "elapsed_ms": cached["latency_ms"], "outcome": "hit"}],
                    cache_hit=True,
                    similarity=cached["similarity"]
                )
                c.execute("UPDATE jobs SET result=%s,sources=%s,input_level=%s,state='completed',stage='已完成（命中语义缓存）',progress=100,lease_until=NULL,finished_at=now() WHERE id=%s AND run_version=%s",
                    (as_jsonb(result), as_jsonb(cached["sources"]), job["input_level"], job["id"], job["run_version"]))
                c.execute("INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,'已完成（命中语义缓存）',100,'completed')",
                    (p.tenant_id, job["id"], job["run_version"]))
                return

    from langgraph.checkpoint.postgres import PostgresSaver
    from .pipeline import build_question_workflow,validate_task_execution,SPECIALIST_ROLES
    with PostgresSaver.from_conn_string(load_config().dsn) as saver:
        saver.setup()
        flow=build_question_workflow(saver)
        state=flow.invoke({"job_id":job["id"],"run_version":job["run_version"]},{"configurable":{"thread_id":job["id"]+":"+str(job["run_version"])}})
    validate_task_execution(state,"最终权限核验",98)
    with database_connection() as c:
        row=fetch_one(c,"SELECT * FROM jobs WHERE id=%s FOR UPDATE",(job["id"],))
        if row["state"]!="running" or row["run_version"]!=job["run_version"] or row["cancel_requested"]:raise Denied("任务执行版本已失效","STALE",409)
        p=load_principal(c,row["owner_id"])
        if p.version!=row["owner_version"] or not sources_valid(c,p,state["sources"]):raise Denied("人员或来源权限已变化","SOURCE_CHANGED",409)
        citations=[{k:e[k] for k in ("document_id","version","title","quote","page","chunk_id")} for e in state["evidence"]]
        result=dict(answer=state["answer"],citations=citations,specialists=[{"name":SPECIALIST_ROLES[r][0],"count":sum(f["role"]==SPECIALIST_ROLES[r][0] for f in state["findings"])} for r in state["route"]],
           missing=state["missing"],hard_block=state["hard_block"],external_blocked=state["external_blocked"],model_calls=state["model_calls"],usage=state["usage"])
        status="review" if state["review"] else "completed";stage="等待业务负责人审核" if state["review"] else "已完成"
        c.execute("UPDATE jobs SET result=%s,sources=%s,input_level=%s,state=%s,stage=%s,progress=100,lease_until=NULL,finished_at=now() WHERE id=%s AND run_version=%s",
            (as_jsonb(result),as_jsonb(state["sources"]),state["input_level"],status,stage,job["id"],job["run_version"]))
        c.execute("INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,100,%s)",(p.tenant_id,job["id"],job["run_version"],stage,status))

    # 权威核验答案自动灌入语义缓存，赋能后续高频并发命中
    if not state.get("hard_block") and not state.get("review") and state.get("answer"):
        try:
            cache.put(job["tenant_id"], job["question"], state["answer"], citations, state.get("sources", []), job["input_level"])
        except Exception:
            pass
def heartbeat():
    with database_connection() as c:
        c.execute("INSERT INTO worker_heartbeats(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET updated_at=now()",(WORKER,))
        c.execute("DELETE FROM worker_heartbeats WHERE updated_at<now()-interval '1 day'")
def run_job(job):
    done=threading.Event();thread=threading.Thread(target=renew_job_lease,args=(job,done),daemon=True);thread.start()
    try:
        if job["kind"]=="ingest":process_ingestion_job(job)
        else:process_question_job(job)
    except Exception as exc:
        message=exc.message if isinstance(exc,Denied) else "任务处理失败，请重试或联系管理员"
        with database_connection() as c:
            row=fetch_one(c,"SELECT * FROM jobs WHERE id=%s FOR UPDATE",(job["id"],))
            if row and row["state"]=="running" and row["run_version"]==job["run_version"]:
                status="cancelled" if row["cancel_requested"] else "failed"
                c.execute("UPDATE jobs SET state=%s,error=%s,stage=%s,finished_at=now(),lease_until=NULL WHERE id=%s",(status,message,"已取消" if status=="cancelled" else "处理失败",job["id"]))
                c.execute("INSERT INTO task_events(tenant_id,job_id,run_version,stage,progress,status) VALUES(%s,%s,%s,%s,100,%s)",(job["tenant_id"],job["id"],job["run_version"],message,status))
                if job["kind"]=="ingest":c.execute("UPDATE documents SET state='failed',error=%s WHERE id=%s AND current_version=%s",(message,job["payload"]["document_id"],job["payload"]["version"]))
        print("任务结束",job["id"],type(exc).__name__,flush=True)
    finally:done.set();thread.join(timeout=2)
def process_next_job():
    job=claim_next_job()
    if not job:return False
    run_job(job)
    return True
def main():
    signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    signal.signal(signal.SIGINT,lambda *_:STOP.set())
    print("企业任务进程已启动",flush=True)
    cfg=load_config()
    if cfg.kafka_enabled:
        # Kafka 为主：relay 投递 outbox + 消费认领；数据库轮询降频保留为对账兜底。
        from . import queue
        queue._worker_id = WORKER
        threading.Thread(target=queue.relay_loop,daemon=True).start()
        threading.Thread(target=queue.consume_forever,args=(run_job,sweep),daemon=True).start()
        idle=3
    else:idle=1
    while not STOP.is_set():
        try:
            heartbeat()
            if not process_next_job():STOP.wait(idle)
        except Exception as exc:
            print("任务进程正在重连",type(exc).__name__,flush=True);STOP.wait(3)
def sweep():
    heartbeat()
    process_next_job()
if __name__=="__main__":main()
