"""Durable single-owner task service with replayable events and bounded admission.

SQLite transactions persist tasks/events/idempotency before a worker starts.
A flock enforces one API process for the local mutable knowledge index.
Restarted running jobs fail explicitly; they are never silently replayed.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import fcntl
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Optional

from ..config import get_settings
from ..schemas.common import PermissionContext
from ..schemas.research import ResearchTask, TaskStatus

class AdmissionError(RuntimeError):
    pass

class IdempotencyConflict(ValueError):
    pass

class TaskRecord:
    def __init__(self, row: sqlite3.Row, events=None):
        self.task = ResearchTask.model_validate_json(row["task_json"])
        self.task.status = TaskStatus(row["status"])
        self.result = json.loads(row["result_json"]) if row["result_json"] else None
        self.error = row["error"] or ""
        self.finished = bool(row["finished"])
        self.created_at = row["created_at"]
        self.events = events or []
        self.review = json.loads(row["review_json"] or "{}")
    def to_dict(self):
        return {"task_id": self.task.task_id, "question": self.task.question,
                "status": self.task.status.value, "depth": self.task.depth.value,
                "created_at": self.created_at, "finished": self.finished,
                "error": self.error, "events": len(self.events), "review": self.review}

class TaskManager:
    def __init__(self, workflow=None, db_path: Optional[Path] = None, settings=None):
        self.settings = settings or get_settings()
        self.workflow = workflow
        self.db_path = Path(db_path or self.settings.data_dir / "state" / "tasks.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lockfile = self.db_path.with_suffix(".owner").open("a+")
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockfile.close()
            raise RuntimeError("Local task storage requires exactly one API process")
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ecom-task")
        self._futures = {}
        with self._db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, task_json TEXT NOT NULL,
                    permission_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, finished INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT, error TEXT NOT NULL DEFAULT '', review_json TEXT,
                    cancelled INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT,
                    fingerprint TEXT, UNIQUE(scope,idempotency_key));
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
            """)
            # No concurrent owner exists; work that died in flight must be explicit.
            interrupted = c.execute("SELECT id FROM tasks WHERE status IN ('processing','pending')").fetchall()
            c.execute("UPDATE tasks SET status='failed',finished=1,error='worker_restarted' "
                      "WHERE status IN ('processing','pending')")
            for row in interrupted:
                c.execute("INSERT INTO events(task_id,payload,created_at) VALUES(?,?,?)",
                          (row["id"], json.dumps({"stage":"error","error":"worker_restarted"}),time.time()))

    @contextmanager
    def _db(self):
        c=sqlite3.connect(str(self.db_path),timeout=15)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def scope(task):
        return json.dumps([task.org_tag,task.user_id],ensure_ascii=False,separators=(",",":"))

    def _ensure_workflow(self):
        if self.workflow is None:
            from ..agents.workflow import get_workflow
            self.workflow=get_workflow()
        return self.workflow

    def submit(self, task, permission=None, idempotency_key=None):
        permission=permission or PermissionContext(user_id=task.user_id,org_tag=task.org_tag)
        payload=task.model_dump(mode="json",exclude={"task_id","created_at","status","estimated_time","error"})
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self._lock, self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                prior=c.execute("SELECT * FROM tasks WHERE scope=? AND idempotency_key=?",
                                (self.scope(task),idempotency_key)).fetchone()
                if prior:
                    if prior["fingerprint"] != digest:
                        raise IdempotencyConflict("同一幂等键对应不同请求")
                    return self.get(prior["id"])
            active=c.execute("SELECT COUNT(*) FROM tasks WHERE finished=0 OR status='awaiting_review'").fetchone()[0]
            if active>=self.settings.task_max_pending:
                raise AdmissionError("任务队列已满，请稍后重试")
            task.status=TaskStatus.PENDING
            c.execute("INSERT INTO tasks(id,scope,task_json,permission_json,status,created_at,"
                      "idempotency_key,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                      (task.task_id,self.scope(task),task.model_dump_json(),permission.model_dump_json(),
                       task.status.value,time.time(),idempotency_key,digest))
        self._append(task.task_id,{"stage":"queued","task_id":task.task_id})
        record=self.get(task.task_id)
        with self._lock:
            self._futures[task.task_id]=self._pool.submit(self._execute,task,permission)
        return record

    def _append(self, task_id, event):
        with self._lock, self._db() as c:
            c.execute("INSERT INTO events(task_id,payload,created_at) VALUES(?,?,?)",
                      (task_id,json.dumps(event,ensure_ascii=False,default=str),time.time()))

    def _update(self,task,status,finished=True,result=None,error="",review=None):
        with self._lock, self._db() as c:
            if self.cancelled(task.task_id):
                status, finished, result = TaskStatus.CANCELLED, True, None
                task.status = status
            c.execute("UPDATE tasks SET status=?,finished=?,result_json=COALESCE(?,result_json),"
                      "error=?,task_json=?,review_json=COALESCE(?,review_json) WHERE id=?",
                      (status.value,int(finished),json.dumps(result,ensure_ascii=False,default=str)
                       if result is not None else None,error,task.model_dump_json(),
                       json.dumps(review,ensure_ascii=False) if review is not None else None,task.task_id))
            if finished:
                payload={"stage":"error" if error else "terminal","status":status.value}
                if error: payload["error"]=error
                c.execute("INSERT INTO events(task_id,payload,created_at) VALUES(?,?,?)",
                          (task.task_id,json.dumps(payload),time.time()))

    def _execute(self,task,permission,decision=None,review=None):
        started=time.monotonic()
        try:
            with self._lock, self._db() as c:
                row=c.execute("SELECT status,cancelled FROM tasks WHERE id=?",(task.task_id,)).fetchone()
                if row["cancelled"] or row["status"]=="cancelled":
                    return
            task.status=TaskStatus.PROCESSING
            self._update(task,task.status,finished=False)
            if permission.user_id.startswith("user_"):
                from ..security.accounts import get_account_store
                account=get_account_store().by_id(permission.user_id)
                if not account or not account["active"]:raise RuntimeError("account_disabled")
                permission=PermissionContext(user_id=account["id"],org_tag=account["org_tag"],roles=[account["role"]])
            wf=self._ensure_workflow()
            if decision is None:
                for event in wf.stream(task,permission):
                    if self.cancelled(task.task_id):
                        raise InterruptedError("cancelled")
                    if time.monotonic()-started>self.settings.task_timeout_seconds:
                        raise TimeoutError("task_deadline_exceeded")
                    self._append(task.task_id,event)
                state=wf.results.get(task.task_id)
            else:
                previous=self.get(task.task_id).result or {}
                state=wf.resume(task,decision)
                state["metrics"]={**previous.get("metrics",{}),**state.get("metrics",{}),
                                  "review_elapsed_s":round(time.monotonic()-started,3)}
            if self.cancelled(task.task_id):
                raise InterruptedError("cancelled")
            if state is None:
                raise RuntimeError("workflow_returned_no_state")
            result=self._state_to_dict(state)
            task.status=state.get("task",task).status
            if task.status==TaskStatus.PROCESSING:
                raise RuntimeError("workflow_has_no_terminal_status")
            if task.status==TaskStatus.FAILED:
                raise RuntimeError("workflow_failed")
            self._update(task,task.status,result=result,review=review)
        except InterruptedError:
            task.status=TaskStatus.CANCELLED
            self._update(task,task.status)
        except Exception:
            # Detailed provider/HTTP errors can contain credentials or private snippets.
            error="task_deadline_exceeded" if time.monotonic()-started>self.settings.task_timeout_seconds else "workflow_failed"
            task.status=TaskStatus.FAILED
            self._update(task,task.status,error=error)
        finally:
            if self.workflow is not None:
                self.workflow.results.pop(task.task_id, None)
                if hasattr(self.workflow, "tracers"):
                    self.workflow.tracers.pop(task.task_id, None)
            with self._lock: self._futures.pop(task.task_id,None)

    def cancelled(self,task_id):
        with self._db() as c:
            row=c.execute("SELECT cancelled FROM tasks WHERE id=?",(task_id,)).fetchone()
            return bool(row and row[0])

    def cancel(self, task_id):
        with self._lock, self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT status FROM tasks WHERE id=?",(task_id,)).fetchone()
            if not row or row[0] not in ("pending","processing"):
                raise ValueError("仅排队或执行中的任务可取消")
            c.execute("UPDATE tasks SET cancelled=1,status='cancelled',finished=1 WHERE id=?",(task_id,))
            c.execute("INSERT INTO events(task_id,payload,created_at) VALUES(?,?,?)",
                      (task_id,json.dumps({"stage":"terminal","status":"cancelled"}),time.time()))
        return self.get(task_id)

    def review(self,task_id,decision,comment,reviewer):
        with self._lock, self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
            if not row or row["status"]!="awaiting_review":
                raise ValueError("任务不处于待复核状态")
            audit={"decision":decision,"comment":comment,"reviewer":reviewer,"time":time.time()}
            record=TaskRecord(row)
            if decision=="reject":
                result=record.result or {}
                report=result.get("report") or {}
                report.update(executive_summary="人工复核拒绝该答案，请补充可信资料后重新提问。",
                              sections=[],key_products=[],recommendations=[],confidence=0,review_required=False)
                result["report"]=report
                c.execute("UPDATE tasks SET status='rejected',finished=1,result_json=?,review_json=? WHERE id=?",
                          (json.dumps(result,ensure_ascii=False),json.dumps(audit,ensure_ascii=False),task_id))
            else:
                c.execute("UPDATE tasks SET status='pending',finished=0,review_json=? WHERE id=?",
                          (json.dumps(audit,ensure_ascii=False),task_id))
        self._append(task_id,{"stage":"review","decision":decision})
        if decision=="approve":
            permission=PermissionContext.model_validate_json(row["permission_json"])
            with self._lock:
                self._futures[task_id]=self._pool.submit(self._execute,record.task,permission,"approve",audit)
        return self.get(task_id)

    def events_after(self,task_id,after=0):
        with self._db() as c:
            rows=c.execute("SELECT id,payload FROM events WHERE task_id=? AND id>? ORDER BY id",
                           (task_id,after)).fetchall()
        return [{"event_id":r["id"],**json.loads(r["payload"])} for r in rows]

    def get(self,task_id):
        with self._db() as c:
            row=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        return TaskRecord(row,self.events_after(task_id)) if row else None

    def list_tasks(self,limit=50,user=None):
        with self._db() as c:
            if user and user.has("research:review") and not user.has("admin:all"):
                rows=c.execute("SELECT * FROM tasks WHERE json_extract(task_json,'$.org_tag')=? ORDER BY created_at DESC LIMIT ?",
                               (user.org_tag,limit)).fetchall()
            elif user and not user.has("admin:all"):
                rows=c.execute("SELECT * FROM tasks WHERE scope=? ORDER BY created_at DESC LIMIT ?",
                               (json.dumps([user.org_tag,user.user_id],ensure_ascii=False,separators=(",",":")),limit)).fetchall()
            else: rows=c.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [TaskRecord(r).to_dict() for r in rows]

    async def event_stream(self,task_id,after=0,heartbeat=15.0):
        last=time.monotonic()
        while True:
            batch=self.events_after(task_id,after)
            for event in batch:
                after=event["event_id"]
                last=time.monotonic()
                yield event
            record=self.get(task_id)
            if record is None or record.finished:
                # Recheck after reading finished flag to cover commit races.
                for event in self.events_after(task_id,after):
                    yield event
                return
            if time.monotonic()-last>=heartbeat:
                yield {"stage":"heartbeat"}
                last=time.monotonic()
            await asyncio.sleep(0.2)

    @staticmethod
    def _state_to_dict(state):
        if state is None:return None
        def dump(v):
            return v.model_dump(mode="json") if hasattr(v,"model_dump") else v
        report=state.get("report")
        source_ids={x.doc_id for x in (state.get("chunks") or []) if x.doc_id}
        source_ids.update(x.id for x in (state.get("documents") or []) if x.id)
        source_ids.update(x.source_id for x in (state.get("evidence_pool") or []) if x.source_id and not x.source_id.startswith("business:"))
        return {"protected_sources":sorted(source_ids),"task":dump(state.get("task")),"plan":dump(state.get("plan")),
                "documents":len(state.get("documents") or []),"chunks":len(state.get("chunks") or []),
                "hypotheses":[dump(h) for h in state.get("hypotheses",[])],
                "report":dump(report),"trace":state.get("trace") or [],
                "metrics":state.get("metrics") or {},"specialists":state.get("specialists") or [],
                "quality":state.get("quality") or {},"business":state.get("business") or {},"review":state.get("review_decision") or ""}

    def close(self):
        self._pool.shutdown(wait=True,cancel_futures=True)
        fcntl.flock(self._lockfile,fcntl.LOCK_UN)
        self._lockfile.close()

_manager=None
_manager_lock=threading.Lock()
def get_task_manager():
    global _manager
    with _manager_lock:
        if _manager is None: _manager=TaskManager()
    return _manager
