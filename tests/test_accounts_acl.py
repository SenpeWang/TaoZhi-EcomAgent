"""账号、企业边界、文档 ACL 及撤销授权后的真实 HTTP 验证。"""
import hashlib,json,time
import pytest
from fastapi.testclient import TestClient
from ecom_copilot.security.accounts import AccountStore,AccountError,LoginError,LoginLocked,password_hash,password_matches
from ecom_copilot.security.auth import User
from ecom_copilot.schemas.common import PermissionContext
from ecom_copilot.security.access import AccessStore

PASSWORD="Strong-initial-password-2026"
@pytest.fixture
def portal(tmp_path,monkeypatch):
    from ecom_copilot import config
    from ecom_copilot.security import auth,accounts,access
    from ecom_copilot.api import legacy_main as main, tasks
    from ecom_copilot.retrieval.engine import RetrievalEngine
    from ecom_copilot.retrieval import engine as engine_module
    from ecom_copilot.api.tasks import TaskManager
    from test_enterprise import FakeWorkflow
    cfg=config.get_settings().model_copy(update={"data_dir":tmp_path/"data","checkpoint_db":tmp_path/"checkpoint.sqlite",
          "auth_enabled":True,"app_env":"production","jwt_secret":"test-secret-"*5,"hyde_enabled":False})
    for module in (config,auth,accounts,access,main):
        monkeypatch.setattr(module,"get_settings",lambda:cfg)
    a=accounts.get_account_store()
    records={}
    for username,role,org in [("admin","admin","A"),("boss","boss","A"),("employee","employee","A"),
                              ("colleague","employee","A"),("otherboss","boss","B")]:
        records[username]=a.create(username,PASSWORD,role,org,must_change=False)
    engine=RetrievalEngine(cfg);monkeypatch.setattr(engine_module,"_engine",engine)
    m=TaskManager(FakeWorkflow(),tmp_path/"tasks.sqlite",cfg);monkeypatch.setattr(tasks,"_manager",m)
    app=main.create_app();app.dependency_overrides[config.get_settings]=lambda:cfg
    # Dependency captures original get_settings; override the signature's original callable as well.
    for dep in [auth.current_user.__defaults__[-1].dependency]:
        app.dependency_overrides[dep]=lambda:cfg
    client=TestClient(app)
    tokens={}
    for username in records:
        r=client.post("/api/auth/login",json={"username":username,"password":PASSWORD})
        assert r.status_code==200,r.text
        tokens[username]=r.json()["access_token"]
    def headers(name):return {"Authorization":"Bearer "+tokens[name]}
    yield client,headers,records,a,engine,m,cfg
    m.close()

def test_password_hash_and_limits():
    hashed=password_hash(PASSWORD)
    assert PASSWORD not in hashed and password_matches(PASSWORD,hashed)
    assert not password_matches("wrong",hashed)
    with pytest.raises(AccountError):password_hash("short")
def test_login_lock_and_unknown_user(tmp_path):
    a=AccountStore(tmp_path/"auth.sqlite");a.create("admin",PASSWORD,"admin","A")
    for _ in range(5):
        with pytest.raises(LoginError):a.login("admin","wrong","127.0.0.1")
    with pytest.raises(LoginLocked):a.login("admin",PASSWORD,"another-ip")
    with pytest.raises(LoginError):a.login("does-not-exist","wrong","different-ip")
def test_last_manager_cannot_be_disabled_or_demoted(tmp_path):
    a=AccountStore(tmp_path/"auth.sqlite");r=a.create("admin",PASSWORD,"admin","A")
    u=User(user_id=r["id"],org_tag="A",roles=["admin"])
    with pytest.raises(AccountError):a.update(u,r["id"],active=False)
    with pytest.raises(AccountError):a.update(u,r["id"],role="employee")
def test_login_public_response_and_legacy_token_disabled(portal):
    c,h,r,a,e,m,cfg=portal
    out=c.get("/api/auth/me",headers=h("employee")).json()
    assert out["roles"]==["employee"] and "password_hash" not in out and "session_id" not in out
    from ecom_copilot.security.auth import create_token
    old=create_token(User(user_id="u",roles=["admin"],is_admin=True),cfg)
    assert c.get("/api/auth/me",headers={"Authorization":"Bearer "+old}).status_code==401
@pytest.mark.parametrize("method,path,payload",[
 ("post","/api/users",{"username":"hacker","password":PASSWORD,"role":"boss"}),
 ("patch","/api/users/fake",{"role":"boss"}),
 ("post","/api/knowledge/documents",{"title":"x","text":"x"}),
 ("patch","/api/knowledge/documents/fake",{"text":"x"}),
 ("delete","/api/knowledge/documents/fake",None),
 ("patch","/api/knowledge/documents/fake/permissions",{"visibility":"company"}),
 ("post","/api/knowledge/ingest",{}),
 ("post","/api/research/task/fake/review",{"decision":"approve"}),
 ("get","/api/users",None),("get","/api/audit",None),
 ("get","/api/metrics",None),("get","/api/graph/vis",None),
])
def test_employee_management_is_denied(portal,method,path,payload):
    c,h,*_=portal
    kwargs={"headers":h("employee")}
    if payload is not None:kwargs["json"]=payload
    assert c.request(method,path,**kwargs).status_code==403
def add_document(c,h,name="admin",visibility="management",users=None):
    response=c.post("/api/knowledge/documents",headers=h(name),
        json={"title":"隐私采购定价","text":"秘密采购价格为 883721 元。供应商名单属于内部隐私。",
              "visibility":visibility,"allowed_users":users or []})
    assert response.status_code==201,response.text
    return response.json()["doc_id"]
def test_privacy_not_visible_to_same_company_employee(portal):
    c,h,r,a,e,m,cfg=portal;doc=add_document(c,h)
    assert c.get("/api/knowledge/documents",headers=h("employee")).json()["documents"]==[]
    assert c.get("/api/knowledge/documents/"+doc,headers=h("employee")).status_code==404
    assert c.post("/api/knowledge/search",headers=h("employee"),json={"query":"秘密采购价格"}).json()["hits"]==[]
    assert c.get("/api/knowledge/documents/"+doc,headers=h("boss")).status_code==200
    assert c.get("/api/knowledge/documents/"+doc,headers=h("otherboss")).status_code==404
def test_selected_grant_revoke_cache_and_history(portal):
    c,h,r,a,e,m,cfg=portal
    doc=add_document(c,h,visibility="selected",users=[r["employee"]["id"]])
    assert c.get("/api/knowledge/documents/"+doc,headers=h("employee")).status_code==200
    assert c.get("/api/knowledge/documents/"+doc,headers=h("colleague")).status_code==404
    assert c.post("/api/knowledge/search",headers=h("employee"),json={"query":"秘密采购价格"}).json()["hits"]
    from ecom_copilot.schemas.research import ResearchTask
    t=ResearchTask(question="秘密采购价格",user_id=r["employee"]["id"],org_tag="A")
    m.submit(t);from test_enterprise import finished
    finished(m,t.task_id)
    with m._db() as db:
        db.execute("UPDATE tasks SET result_json=? WHERE id=?",
          (json.dumps({"protected_sources":[doc],"report":{"executive_summary":"秘密883721"}}),t.task_id))
    assert c.get("/api/research/task/"+t.task_id,headers=h("employee")).json()["result"]["report"]
    response=c.patch("/api/knowledge/documents/"+doc+"/permissions",headers=h("boss"),json={"visibility":"management"})
    assert response.status_code==200
    assert c.post("/api/knowledge/search",headers=h("employee"),json={"query":"秘密采购价格"}).json()["hits"]==[]
    old=c.get("/api/research/task/"+t.task_id,headers=h("employee"))
    assert old.json()["result"]["report"] is None and "秘密883721" not in old.text
def test_company_documents_stay_tenant_scoped(portal):
    c,h,*_=portal;doc=add_document(c,h,visibility="company")
    assert c.get("/api/knowledge/documents/"+doc,headers=h("employee")).status_code==200
    assert c.get("/api/knowledge/documents/"+doc,headers=h("otherboss")).status_code==404
    assert c.patch("/api/knowledge/documents/"+doc+"/permissions",headers=h("otherboss"),json={"visibility":"company"}).status_code==404
def test_grantee_must_be_valid_same_company_user(portal):
    c,h,r,*_=portal
    bad=c.post("/api/knowledge/documents",headers=h("admin"),json={"title":"x","text":"x","visibility":"selected",
                                                                  "allowed_users":[r["otherboss"]["id"]]})
    assert bad.status_code==400
    assert c.post("/api/knowledge/documents",headers=h("admin"),json={"title":"x","text":"x","org_tag":"B"}).status_code==422
def test_document_edit_delete_and_audit(portal):
    c,h,*_=portal;doc=add_document(c,h)
    assert c.patch("/api/knowledge/documents/"+doc,headers=h("boss"),json={"title":"更新标题","text":"采购价格已更新为 7 元"}).status_code==200
    assert "7 元" in c.get("/api/knowledge/documents/"+doc,headers=h("admin")).json()["text"]
    assert c.delete("/api/knowledge/documents/"+doc,headers=h("admin")).status_code==200
    assert c.get("/api/knowledge/documents/"+doc,headers=h("boss")).status_code==404
    actions=[e["action"] for e in c.get("/api/audit",headers=h("boss")).json()["events"]]
    assert "删除文档" in actions and "编辑文档" in actions
def test_disabled_role_changed_and_logout_sessions_invalidate(portal):
    c,h,r,a,e,m,cfg=portal
    assert c.patch("/api/users/"+r["employee"]["id"],headers=h("boss"),json={"active":False}).status_code==200
    assert c.get("/api/auth/me",headers=h("employee")).status_code==401
    assert c.post("/api/auth/login",json={"username":"employee","password":PASSWORD}).status_code==401
    assert c.patch("/api/users/"+r["admin"]["id"],headers=h("boss"),json={"role":"employee"}).status_code==200
    assert c.get("/api/users",headers=h("admin")).status_code==401
    assert c.post("/api/auth/logout",headers=h("boss")).status_code==200
    assert c.get("/api/auth/me",headers=h("boss")).status_code==401
def test_password_change_invalidates_old_session(portal):
    c,h,r,a,e,m,cfg=portal
    assert c.post("/api/auth/password",headers=h("employee"),json={"current_password":PASSWORD,"new_password":"Different-password-2026"}).status_code==200
    assert c.get("/api/auth/me",headers=h("employee")).status_code==401
    assert c.post("/api/auth/login",json={"username":"employee","password":PASSWORD}).status_code==401
    assert c.post("/api/auth/login",json={"username":"employee","password":"Different-password-2026"}).status_code==200
def test_new_account_forces_password_change(portal):
    c,h,*_=portal
    assert c.post("/api/users",headers=h("admin"),json={"username":"newuser","password":PASSWORD}).status_code==201
    login=c.post("/api/auth/login",json={"username":"newuser","password":PASSWORD}).json()
    assert login["user"]["must_change_password"]
    assert c.get("/api/knowledge/documents",headers={"Authorization":"Bearer "+login["access_token"]}).status_code==403
def test_signed_claim_roles_never_override_current_account_role(portal):
    c,h,r,a,e,m,cfg=portal
    from ecom_copilot.security.auth import create_token
    sid,_=a.create_session(r["employee"])
    token=create_token(User(user_id=r["employee"]["id"],org_tag="B",roles=["boss"],is_admin=True,session_id=sid),cfg)
    assert c.get("/api/users",headers={"Authorization":"Bearer "+token}).status_code==403
def test_query_employee_can_ask_but_other_owner_tasks_hidden(portal):
    c,h,r,a,e,m,cfg=portal
    response=c.post("/api/research/task",headers=h("employee"),json={"question":"查询商品参数"})
    assert response.status_code==202
    tid=response.json()["task_id"]
    assert c.get("/api/research/task/"+tid,headers=h("colleague")).status_code==404
    assert c.get("/api/research/task/"+tid,headers=h("boss")).status_code==200
    assert c.get("/api/research/task/"+tid,headers=h("otherboss")).status_code==404
def test_legacy_private_docs_not_company_wide(tmp_path):
    from ecom_copilot.schemas.common import TextChunk
    store=AccessStore(tmp_path/"acl.sqlite")
    store.migrate_chunks([TextChunk(doc_id="private",org_tag="A",is_public=False),TextChunk(doc_id="public",is_public=True)],"A")
    employee=PermissionContext(user_id="e",org_tag="A",roles=["employee"])
    boss=PermissionContext(user_id="b",org_tag="A",roles=["boss"])
    assert not store.can_read("private",employee) and store.can_read("private",boss)
    assert store.can_read("public",employee)

def test_role_demotion_invalidates_cached_private_hits_even_for_old_context(portal):
    c,h,r,a,e,m,cfg=portal;doc=add_document(c,h)
    old=PermissionContext(user_id=r["admin"]["id"],org_tag="A",roles=["admin"])
    assert e.retrieve("秘密采购价格",permission=old)
    boss=User(user_id=r["boss"]["id"],org_tag="A",roles=["boss"])
    a.update(boss,r["admin"]["id"],role="employee")
    assert e.retrieve("秘密采购价格",permission=old)==[]

def test_authenticated_empty_retrieval_does_not_collect_or_inject_legacy_memory(portal,monkeypatch):
    c,h,r,a,e,m,cfg=portal
    from ecom_copilot.agents.workflow import ResearchWorkflow
    from ecom_copilot.agents.supervisor import decide_route
    from ecom_copilot.schemas.research import ResearchTask
    from ecom_copilot.memory import HistoryInjector
    monkeypatch.setattr(HistoryInjector,"build",lambda **kw:pytest.fail("unversioned memory reached enterprise prompt"))
    wf=object.__new__(ResearchWorkflow);wf.settings=cfg;wf.engine=e
    state=wf._entry_state(ResearchTask(question="隐私资料",user_id=r["employee"]["id"],org_tag="A"),
                          PermissionContext(user_id=r["employee"]["id"],org_tag="A",roles=["employee"]))
    assert state.get("session_history","")==""
    assert decide_route(state)=="specialist_review"
