"""Regression coverage for isolation, durability, partial failures and review."""
import asyncio
import threading
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from ecom_copilot.config import get_settings, Settings
from ecom_copilot.api.tasks import TaskManager, IdempotencyConflict, AdmissionError
from ecom_copilot.schemas.research import ResearchTask, TaskStatus, ResearchReport, ReportSection
from ecom_copilot.schemas.common import Evidence, Citation, PermissionContext, SourceDocument

class FakeWorkflow:
    def __init__(self,block=None,fail=False):
        self.results={}; self.calls=0; self.block=block; self.fail=fail
    def stream(self,task,permission):
        self.calls+=1
        if self.block:self.block.wait(2)
        if self.fail:raise RuntimeError("secret-provider-response")
        yield {"stage":"one"}
        task.status=TaskStatus.COMPLETED
        self.results[task.task_id]={"task":task,"report":ResearchReport(executive_summary="ok")}
        yield {"stage":"done"}
    def resume(self,task,decision):
        task.status=TaskStatus.COMPLETED
        return {"task":task,"report":ResearchReport(executive_summary="reviewed")}

def finished(manager,id):
    for _ in range(200):
        r=manager.get(id)
        if r.finished:return r
        time.sleep(.01)
    pytest.fail("worker did not finish")

def test_durable_result_and_idempotency(tmp_path):
    path=tmp_path/"tasks.sqlite";wf=FakeWorkflow()
    m=TaskManager(wf,path)
    t=ResearchTask(question="参数",user_id="u",org_tag="a")
    try:
        r=m.submit(t,idempotency_key="k")
        finished(m,t.task_id)
        duplicate=m.submit(ResearchTask(question="参数",user_id="u",org_tag="a"),idempotency_key="k")
        assert duplicate.task.task_id==t.task_id and wf.calls==1
        with pytest.raises(IdempotencyConflict):
            m.submit(ResearchTask(question="其他",user_id="u",org_tag="a"),idempotency_key="k")
        other=m.submit(ResearchTask(question="参数",user_id="u",org_tag="b"),idempotency_key="k")
        assert other.task.task_id!=t.task_id
        finished(m,other.task.task_id)
    finally:m.close()
    m=TaskManager(FakeWorkflow(),path)
    try:assert m.get(t.task_id).result["report"]["executive_summary"]=="ok"
    finally:m.close()

def test_sse_broadcast_resume(tmp_path):
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    try:
        t=ResearchTask(question="q");m.submit(t);finished(m,t.task_id)
        async def collect(after=0):return [e async for e in m.event_stream(t.task_id,after=after)]
        a=asyncio.run(collect());b=asyncio.run(collect())
        assert a==b
        assert len({e["event_id"] for e in a})==len(a)
        replay=asyncio.run(collect(a[0]["event_id"]))
        assert replay==a[1:]
    finally:m.close()

def test_queue_limit_and_cancel(tmp_path):
    block=threading.Event();wf=FakeWorkflow(block)
    cfg=get_settings().model_copy(update={"task_max_pending":1})
    m=TaskManager(wf,tmp_path/"db",cfg)
    try:
        t=ResearchTask(question="q");m.submit(t)
        with pytest.raises(AdmissionError):m.submit(ResearchTask(question="other"))
        m.cancel(t.task_id);block.set()
        for _ in range(100):
            if not m._futures:break
            time.sleep(.01)
        assert m.get(t.task_id).task.status==TaskStatus.CANCELLED
        assert m.get(t.task_id).result is None
    finally:block.set();m.close()

def test_no_concurrent_sqlite_owner(tmp_path):
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    try:
        with pytest.raises(RuntimeError,match="one API process"):TaskManager(FakeWorkflow(),tmp_path/"db")
    finally:m.close()

def test_error_redacted(tmp_path):
    m=TaskManager(FakeWorkflow(fail=True),tmp_path/"db")
    try:
        t=ResearchTask(question="q");m.submit(t);r=finished(m,t.task_id)
        assert r.task.status==TaskStatus.FAILED
        assert "secret-provider" not in str(r.events)+r.error
    finally:m.close()

def test_restart_running_job_is_not_replayed(tmp_path):
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    t=ResearchTask(question="q")
    with m._db() as c:
        c.execute("INSERT INTO tasks(id,scope,task_json,permission_json,status,created_at) VALUES(?,?,?,?,?,?)",
                  (t.task_id,m.scope(t),t.model_dump_json(),"{}","processing",time.time()))
    m.close()
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    try:assert m.get(t.task_id).error=="worker_restarted" and m.workflow.calls==0
    finally:m.close()

def test_review_reject_and_repeat_conflict(tmp_path):
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    try:
        t=ResearchTask(question="q");m.submit(t);finished(m,t.task_id)
        with m._db() as c:c.execute("UPDATE tasks SET status='awaiting_review' WHERE id=?",(t.task_id,))
        r=m.review(t.task_id,"reject","证据不够","reviewer")
        assert r.task.status==TaskStatus.REJECTED
        assert not r.result["report"]["sections"]
        assert r.review["reviewer"]=="reviewer"
        with pytest.raises(ValueError):m.review(t.task_id,"approve","","reviewer")
    finally:m.close()

def test_review_approve_updates_stored_result(tmp_path):
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    try:
        t=ResearchTask(question="q");m.submit(t);finished(m,t.task_id)
        with m._db() as c:c.execute("UPDATE tasks SET status='awaiting_review' WHERE id=?",(t.task_id,))
        m.review(t.task_id,"approve","已核对","reviewer")
        r=finished(m,t.task_id)
        assert r.task.status==TaskStatus.COMPLETED
        assert r.result["report"]["executive_summary"]=="reviewed"
    finally:m.close()

def test_subagent_timeout_returns_and_late_result_not_published():
    from ecom_copilot.agents.subagents import SubAgentPool,SubAgentSpec
    from ecom_copilot.ingestion.base import SourceAdapter
    class Slow(SourceAdapter):
        name="slow"
        def fetch(self,request):
            time.sleep(.15)
            return [SourceDocument(title="late")]
    pool=SubAgentPool(1);h=pool.spawn(SubAgentSpec("s","query",Slow(),summarize=False))
    started=time.monotonic();pool.wait([h],timeout=.02)
    assert time.monotonic()-started<.1 and h.status=="timeout"
    time.sleep(.2)
    assert h.documents==[] and h.status=="timeout"

def test_no_arbitrary_citation_repair():
    from ecom_copilot.agents.report_generation import _enforce_citations
    r=ResearchReport(sections=[ReportSection(content="unsupported",citation_indices=[999])])
    _enforce_citations(r,[Citation(index=0,quote="unrelated")])
    assert r.sections[0].citation_indices==[]

def test_quality_gate_flags_fabricated_number():
    from ecom_copilot.agents.quality_gate import assess
    e=Evidence(doc_name="规格",quote="切幅 320mm")
    r=ResearchReport(executive_summary="切幅999mm[0]",
        sections=[ReportSection(content="切幅999mm[0]",citation_indices=[0])],
        citations=[Citation(index=0,quote="切幅 320mm")])
    result=assess({"task":ResearchTask(question="切幅"),"evidence_pool":[e],"report":r})
    assert not result["passed"] and "答案含原文未支持的数字" in result["reasons"]

def test_quality_gate_realtime_stock_requires_business_system():
    from ecom_copilot.agents.quality_gate import assess
    e=Evidence(doc_name="参数",quote="库存不是实时数据")
    r=ResearchReport(sections=[ReportSection(content="请核实[0]",citation_indices=[0])],
                     citations=[Citation(index=0,quote=e.quote)])
    result=assess({"task":ResearchTask(question="库存还有吗"),"evidence_pool":[e],"report":r})
    assert any("实时核实" in x for x in result["reasons"])

def test_synthetic_and_private_evidence_cannot_reach_specialists():
    from ecom_copilot.agents.specialists import trusted_evidence
    d=SourceDocument(source="synthetic")
    state={"permission":PermissionContext(user_id="a",org_tag="x"),"documents":[d],
        "evidence_pool":[Evidence(source_id=d.id,doc_name="fake",quote="fake"),
                         Evidence(doc_name="private",quote="secret",owner_id="b",org_tag="y",is_public=False)]}
    assert trusted_evidence(state)==[]

@pytest.mark.parametrize("fields",[{"question":" "},{"question":"x","max_sources":1000},{"question":"x","depth":"invalid"}])
def test_request_limits(fields):
    from ecom_copilot.api.routes.research import CreateTaskRequest
    with pytest.raises(ValueError):CreateTaskRequest(**fields)

@pytest.mark.parametrize("updates",[{"auth_enabled":False},{"jwt_secret":"short"},{"synthetic_fallback_enabled":True}])
def test_production_configuration_fail_closed(updates,tmp_path):
    values=dict(app_env="production",auth_enabled=True,jwt_secret="x"*40,synthetic_fallback_enabled=False,
                data_dir=tmp_path/"data",reports_dir=tmp_path/"reports",audit_dir=tmp_path/"audit",trace_dir=tmp_path/"trace")
    values.update(updates)
    with pytest.raises(ValueError):Settings(_env_file=None,**values)

def test_api_identity_ownership_and_roles(tmp_path,monkeypatch):
    from ecom_copilot.api.legacy_main import create_app
    from ecom_copilot.api import tasks
    from ecom_copilot.security import auth
    from ecom_copilot.security.auth import User,current_user,create_token
    cfg=get_settings().model_copy(update={"auth_enabled":True,"jwt_secret":"s"*40})
    m=TaskManager(FakeWorkflow(),tmp_path/"db")
    monkeypatch.setattr(tasks,"_manager",m)
    monkeypatch.setattr(auth,"get_settings",lambda:cfg)
    app=create_app()
    app.dependency_overrides[get_settings]=lambda:cfg
    user=User(user_id="a",org_tag="x",roles=["analyst"])
    app.dependency_overrides[current_user]=lambda:user
    client=TestClient(app)
    try:
        assert client.post("/api/research/task",json={"question":"q","user_id":"b"}).status_code==403
        response=client.post("/api/research/task",json={"question":"q"})
        assert response.status_code==202
        id=response.json()["task_id"];finished(m,id)
        other=User(user_id="b",org_tag="y",roles=["analyst"])
        app.dependency_overrides[current_user]=lambda:other
        assert client.get("/api/research/task/"+id).status_code==404
        assert client.get("/api/research/task/"+id+"/stream").status_code==404
        assert client.get("/api/research/tasks").json()==[]
        assert client.post("/api/research/task/"+id+"/review",json={"decision":"approve"}).status_code==403
        assert client.post("/api/auth/token",json={"is_admin":True}).status_code==404
        assert client.get("/api/graph/vis").status_code==403
        assert client.post("/api/mcp/tools",json={"tool_name":"search_knowledge","user_context":{"user_id":"a"},"arguments":{"query":"q"}}).status_code==403
        app.dependency_overrides.pop(current_user)
        assert client.get("/api/research/task/"+id).status_code==401
        token=create_token(user,cfg)
        assert client.get("/api/research/task/"+id,headers={"Authorization":"Bearer "+token}).status_code==200
    finally:m.close()

def test_actual_langgraph_interrupt_and_resume(tmp_path,monkeypatch):
    from ecom_copilot.agents import workflow as module
    from ecom_copilot.agents.workflow import build_graph
    from ecom_copilot.schemas.common import SourceDocument,TextChunk
    from ecom_copilot.schemas.graph import ExtractionResult
    from ecom_copilot.agents.state import initial_state
    from ecom_copilot.schemas.research import TaskPlan
    from langgraph.types import Command
    cfg=get_settings().model_copy(update={"checkpoint_db":tmp_path/"cp.sqlite"})
    def gen(s,config):
        return {"report":ResearchReport(executive_summary="safe",sections=[ReportSection(content="no citation")])}
    monkeypatch.setitem(module.AGENT_NODES,"report_generation",gen)
    graph=build_graph(cfg)
    t=ResearchTask(question="q")
    state=initial_state(t);state.update(plan=TaskPlan(intent="spec_query"),documents=[SourceDocument()],
        chunks=[TextChunk()],extraction=ExtractionResult(),graph_result={"ok":True},
        research_round=100,specialists=[{"role":"product","verdict":"unknown"}])
    config={"configurable":{"thread_id":t.task_id,"runtime":SimpleNamespace(settings=cfg)}}
    graph.invoke(state,config)
    snapshot=graph.get_state(config)
    assert snapshot.next==("quality_gate",)
    graph.invoke(Command(resume="approve"),config)
    snapshot=graph.get_state(config)
    assert not snapshot.next and snapshot.values["review_decision"]=="approve"

@pytest.mark.parametrize("variant",["wrong_tenant","wrong_sku","stale","negative","redirect","valid"])
def test_business_stock_contract(variant):
    from ecom_copilot.business.client import BusinessClient,BusinessUnavailable
    from datetime import datetime,timezone,timedelta
    import httpx
    data={"org_tag":"shop","sku_id":"C3-16","available":10,"price_minor":1990,
          "currency":"CNY","as_of":datetime.now(timezone.utc).isoformat()}
    if variant=="wrong_tenant":data["org_tag"]="other"
    if variant=="wrong_sku":data["sku_id"]="other"
    if variant=="stale":data["as_of"]=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    if variant=="negative":data["available"]=-1
    def handler(request):
        assert request.headers["x-org-tag"]=="shop"
        return httpx.Response(302 if variant=="redirect" else 200,json=data)
    cfg=get_settings().model_copy(update={"business_api_url":"https://erp.example/v1",
                                        "business_api_token":"test"})
    client=BusinessClient(cfg,httpx.MockTransport(handler))
    permission=PermissionContext(user_id="u",org_tag="shop")
    if variant=="valid":assert client.lookup("stock","C3-16",permission).available==10
    else:
        with pytest.raises(BusinessUnavailable):client.lookup("stock","C3-16",permission)

def test_business_order_owner_checked():
    from ecom_copilot.business.client import BusinessClient,BusinessUnavailable
    from datetime import datetime,timezone
    import httpx
    data={"org_tag":"shop","customer_id":"someone_else","order_id":"ORD-1","status":"paid",
          "as_of":datetime.now(timezone.utc).isoformat()}
    cfg=get_settings().model_copy(update={"business_api_url":"https://erp.example/v1","business_api_token":"test"})
    client=BusinessClient(cfg,httpx.MockTransport(lambda request:httpx.Response(200,json=data)))
    with pytest.raises(BusinessUnavailable):
        client.lookup("orders","ORD-1",PermissionContext(user_id="u",org_tag="shop"))

def test_business_missing_integration_fails_explicitly():
    from ecom_copilot.business.client import BusinessClient,BusinessUnavailable
    cfg=get_settings().model_copy(update={"business_api_url":"","business_api_token":""})
    with pytest.raises(BusinessUnavailable,match="未配置"):
        BusinessClient(cfg).lookup("stock","C3",PermissionContext())

def test_query_cache_never_reuses_admin_results():
    from ecom_copilot.retrieval.fusion import QueryCache
    cache=QueryCache()
    admin=PermissionContext(user_id="u",org_tag="shop",is_admin=True)
    analyst=PermissionContext(user_id="u",org_tag="shop",is_admin=False)
    cache.put("q",admin,8,["private"])
    assert cache.get("q",analyst,8) is None

def test_tenant_documents_are_ranked_before_limiting_candidates():
    import numpy as np
    from ecom_copilot.retrieval.channels import BaseChannel
    from ecom_copilot.schemas.common import TextChunk
    chunks=[TextChunk(text="private",owner_id="other",org_tag="other",is_public=False) for _ in range(40)]
    chunks.append(TextChunk(text="mine",owner_id="u",org_tag="shop",is_public=False))
    channel=BaseChannel(SimpleNamespace(chunks=chunks))
    hits=channel._top(np.arange(41,0,-1),1,"test",PermissionContext(user_id="u",org_tag="shop"))
    assert len(hits)==1 and hits[0].chunk.text=="mine"

def test_request_body_limit_before_endpoint():
    from fastapi import FastAPI,Request
    from ecom_copilot.api.limits import RequestBodyLimit
    app=FastAPI()
    calls=[]
    app.add_middleware(RequestBodyLimit,max_upload_bytes=10)
    @app.post("/x")
    async def endpoint(request:Request):
        calls.append(True)
        return {"ok":True}
    c=TestClient(app)
    assert c.post("/x",content=b"x"*(2*1024*1024+1)).status_code==413
    assert not calls

def test_partial_answer_not_written_into_memory(monkeypatch):
    from ecom_copilot.agents.workflow import ResearchWorkflow
    from ecom_copilot.agents import workflow as module
    def forbidden():raise AssertionError("provisional answer reached memory")
    monkeypatch.setattr(module,"get_session_store",forbidden)
    wf=ResearchWorkflow.__new__(ResearchWorkflow);wf.settings=get_settings()
    t=ResearchTask(question="q",status=TaskStatus.AWAITING_REVIEW)
    wf._record_turn(t,{"report":ResearchReport(executive_summary="unverified")})

def test_fabricated_extraction_quote_is_not_a_trusted_source():
    from ecom_copilot.agents.specialists import trusted_evidence
    d=SourceDocument(title="spec",raw_text="切幅 320mm")
    state={"documents":[d],"evidence_pool":[Evidence(source_id=d.id,doc_name="spec",quote="切幅999mm")]}
    assert trusted_evidence(state)==[]
    state["evidence_pool"]=[Evidence(source_id=d.id,doc_name="spec",quote="切幅 320mm")]
    assert len(trusted_evidence(state))==1

def test_forged_business_source_without_verified_snapshot_is_rejected():
    from ecom_copilot.agents.specialists import trusted_evidence
    state={"evidence_pool":[Evidence(source_id="business:sku",doc_name="ERP",quote="fake")]}
    assert trusted_evidence(state)==[]

def test_quick_route_reuses_evidence_without_expensive_hypothesis_loop():
    from ecom_copilot.agents.supervisor import decide_route
    t=ResearchTask(question="参数",depth="quick")
    state={"task":t,"documents":[object()],"chunks":[object()],"extraction":object(),
           "graph_result":{"mode":"retrieval_first"},"evidence_pool":[object()]}
    assert decide_route(state)=="specialist_review"
