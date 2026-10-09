"""仅由远端部署账号运行：私有数据库初始化、演示种子与旧库迁移。"""
import os,sys,json,secrets,subprocess,sqlite3,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from ecom_copilot.enterprise.db import database_connection,fetch_one,fetch_all,as_jsonb
from ecom_copilot.enterprise.services import generate_id
from ecom_copilot.enterprise.passwords import hash_password
from ecom_copilot.enterprise.ingest import split
RUNTIME=Path.home()/".local/share/ecom-agent/runtime"
STATE=Path.home()/".local/share/ecom-agent"
TEMPLATE=[
 ("商品产品部",["商品资料组","产品适配组"]),("电商运营部",["店铺运营组","内容营销组"]),
 ("客服售后部",["售前客服组","售后服务组"]),("采购供应链部",["采购组","仓储物流组"]),
 ("财务综合部",["财务组","行政组"])]
def node_id(tenant,name):return "org_"+hashlib.sha256((tenant+":"+name).encode()).hexdigest()[:24]
def organization(c,tenant,name):
    root=node_id(tenant,"公司")
    c.execute("INSERT INTO org_nodes(id,tenant_id,name,kind) VALUES(%s,%s,%s,'company') ON CONFLICT(id) DO NOTHING",(root,tenant,name))
    nodes={"公司":root}
    for department,teams in TEMPLATE:
        dep=node_id(tenant,department);nodes[department]=dep
        c.execute("INSERT INTO org_nodes(id,tenant_id,parent_id,name,kind) VALUES(%s,%s,%s,%s,'department') ON CONFLICT(id) DO NOTHING",(dep,tenant,root,department))
        for team in teams:
            nid=node_id(tenant,team);nodes[team]=nid
            c.execute("INSERT INTO org_nodes(id,tenant_id,parent_id,name,kind) VALUES(%s,%s,%s,%s,'team') ON CONFLICT(id) DO NOTHING",(nid,tenant,dep,team))
    return nodes
def add_doc(c,tenant,creator,did,title,body,node,scope="company",level=1,state="published",version=1):
    c.execute("INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,download_allowed,state,current_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",(did,tenant,node,creator,title,scope,level,level<3,level==1,state,version))
    c.execute("INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,created_by,policy) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(tenant,did,version,title,body,hashlib.sha256(body.encode()).hexdigest(),creator,as_jsonb(dict(node_id=node,scope=scope,level=level))))
    for ch in split([(0,body)],did,version):
        c.execute("INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(ch["id"],tenant,did,version,ch["ordinal"],ch["page"],ch["text"]))
    from ecom_copilot.enterprise.facts import index_mentions
    index_mentions(c,tenant,did,version,split([(0,body)],did,version))
def corpus():
    path=ROOT/"data/index/chunks.json"
    groups={}
    for chunk in json.loads(path.read_text()) if path.exists() else []:
        groups.setdefault(chunk["doc_id"],[]).append(chunk)
    return groups
def seed_demo():
    from ecom_copilot.enterprise.config import load_config
    if load_config().mode not in ("demo","test"):
        raise RuntimeError("演示初始化仅允许演示或测试环境")
    with database_connection() as c:
        tenant="tenant_demo"
        c.execute("INSERT INTO tenants(id,code,name,mode) VALUES(%s,'mofa','手机配件电商 · 演示企业','demo') ON CONFLICT(id) DO NOTHING",(tenant,))
        nodes=organization(c,tenant,"手机配件电商 · 演示企业")
        users={}
        hashed=hash_password("123456","demo")
        for username,display,admin in [("admin","系统管理员",True),("staff","运营员工",False),("leader","运营组长",False),("boss","公司老板",False)]:
            existing=fetch_one(c,"SELECT id FROM users WHERE tenant_id=%s AND username=%s",(tenant,username))
            user=existing["id"] if existing else generate_id("user");users[username]=user
            c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,system_admin,must_change) VALUES(%s,%s,%s,%s,%s,%s,false) ON CONFLICT(tenant_id,username) DO NOTHING",(user,tenant,username,display,hashed,admin))
        for user,node,role in [(users["staff"],nodes["店铺运营组"],"employee"),(users["leader"],nodes["店铺运营组"],"leader"),(users["boss"],nodes["公司"],"boss")]:
            c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(tenant,user,node,role))
        for did,chunks in corpus().items():
            add_doc(c,tenant,users["boss"],"demo_"+did,chunks[0].get("doc_name","商品资料"),"\n".join(x["text"] for x in chunks),nodes["公司"])
        samples=[
          ("company","企业数据安全与文档密级分级规范","全员遵守企业信息安全准则。员工依岗位授权读取已获准商品资料；组长负责本部门业务审核与文档初审；老板审批跨部门及三级机密授权；管理员负责系统底层与账号运维。","公司","company",1),
          ("team","店铺运营组日常业务作业指导书","店铺运营组负责电商各渠道商品详情维护、上架参数核验与客户售前咨询支持。日常作业必须严格引用官方核准规格，杜绝臆测适配型号或夸大售后承诺。","店铺运营组","org",1),
          ("leader","电商运营组工作职责与内容发布审核规范","运营组长全面负责本组日常运营内容与上架文档的二级合规复核。遇跨部门供应链或涉保修例外事项，应发起跨部门协同审批。","店铺运营组","org",2),
          ("procurement","供应链采购协同与供应商日常管理指引","采购组负责品牌官方配件及核心耗材供应商对接、准入资质审核与出厂批次抽检。非本组经办人员查阅进货明细及供应商底价需单独授权。","采购组","org",2),
          ("boss","企业经营战略与核心商业秘密保护规定","公司核心经营指标、战略采购底价协议及关键供应商股权合作事宜归属三级核心商业秘密，由企业法定代表人统一管理，严格禁止向未经审核的第三方外部模型出境传输。","公司","company",3)]
        for tag,title,body,node,scope,level in samples:add_doc(c,tenant,users["boss"],"demo_doc_"+tag,title,body,nodes[node],scope,level)
    print("独立演示企业与四种身份已初始化")
def sqlite_rows(name,table):
    p=ROOT/"data/state"/name
    if not p.exists():return []
    c=sqlite3.connect(p);c.row_factory=sqlite3.Row
    try:return [dict(r) for r in c.execute("SELECT * FROM "+table)]
    finally:c.close()
def migrate():
    accounts=sqlite_rows("accounts.sqlite","users")
    olddocs=sqlite_rows("document-access.sqlite","documents")
    orgs=sorted(set([r["org_tag"] for r in accounts]+[r["org_tag"] for r in olddocs]))
    groups=corpus()
    with database_connection() as c:
        tenantmap={};usermap={}
        for index,name in enumerate(orgs or ["手机配件公司"]):
            tenant="tenant_"+hashlib.sha256(name.encode()).hexdigest()[:18]
            code="mofa" if name=="手机配件公司" else "org_"+hashlib.sha256(name.encode()).hexdigest()[:8]
            c.execute("INSERT INTO tenants(id,code,name,mode) VALUES(%s,%s,%s,'production') ON CONFLICT(id) DO NOTHING",(tenant,code,name))
            tenantmap[name]=(tenant,organization(c,tenant,name))
        for r in accounts:
            tenant,nodes=tenantmap[r["org_tag"]];usermap[r["id"]]=tenant
            c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,system_admin,active,must_change,version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",(r["id"],tenant,r["username"],r["display_name"],r["password_hash"],r["role"]=="admin",bool(r["active"]),bool(r["must_change"]),r["version"]))
            if r["role"]=="boss":
                c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,'boss') ON CONFLICT DO NOTHING",(tenant,r["id"],nodes["公司"]))
            elif r["role"]=="employee":
                c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,'employee') ON CONFLICT DO NOTHING",(tenant,r["id"],nodes["公司"]))
        for name,(tenant,nodes) in tenantmap.items():
            creator=fetch_one(c,"SELECT id FROM users WHERE tenant_id=%s ORDER BY system_admin DESC LIMIT 1",(tenant,))
            if not creator:continue
            for r in olddocs:
                if r["org_tag"]!=name:continue
                body="\n".join(x["text"] for x in groups.get(r["id"],[])) or "历史资料未找到原文，需重新核验。"
                scope="company" if r["visibility"]=="company" else ("selected" if r["visibility"]=="selected" else "company")
                level=1 if r["visibility"]=="company" else 3
                add_doc(c,tenant,creator["id"],r["id"],("【历史演示】"+r["title"]) if groups.get(r["id"]) and all(x.get("source")=="local_corpus" for x in groups[r["id"]]) else r["title"],body,nodes["公司"],scope,level,"deleted" if r["deleted"] else ("published" if groups.get(r["id"]) else "draft"),max(1,r["version"]))
                for reader in json.loads(r["allowed_users"] or "[]"):
                    if usermap.get(reader)==tenant:
                        c.execute("INSERT INTO document_grants(tenant_id,document_id,user_id,effect,can_read,granted_by) VALUES(%s,%s,%s,'allow',true,%s) ON CONFLICT DO NOTHING",(tenant,r["id"],reader,creator["id"]))
                if not fetch_one(c,"SELECT id FROM audit_events WHERE tenant_id=%s AND target_id=%s AND action='迁移历史文档权限'",(tenant,r["id"])):
                    c.execute("INSERT INTO audit_events(tenant_id,actor_id,action,target_id,node_id,level,system,detail) VALUES(%s,%s,'迁移历史文档权限',%s,%s,%s,true,%s)",(tenant,creator["id"],r["id"],nodes["公司"],level,as_jsonb({"scope":scope,"level":level,"count":len(json.loads(r["allowed_users"] or "[]"))})))
        old_tasks={str(r["id"]):json.loads(r.get("task_json") or "{}") for r in sqlite_rows("tasks.sqlite","tasks")}
        for name,table in [("tasks.sqlite","tasks"),("tasks.sqlite","events"),("accounts.sqlite","audit")]:
            for r in sqlite_rows(name,table):
                task=json.loads(r.get("task_json") or "{}") if table=="tasks" else old_tasks.get(str(r.get("task_id","")),{})
                tenant=usermap.get(task.get("user_id"))
                original_org=r.get("org_tag") or task.get("org_tag")
                if not tenant and original_org in tenantmap:tenant=tenantmap[original_org][0]
                tenant=tenant or next(iter(tenantmap.values()))[0]
                rid=str(r["id"])
                c.execute("INSERT INTO legacy_archive(source,source_id,tenant_id,record) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(name+":"+table,rid,tenant,as_jsonb(r)))
                if table=="tasks" and task.get("user_id") in usermap:
                    user=fetch_one(c,"SELECT * FROM users WHERE id=%s",(task["user_id"],))
                    root=node_id(tenant,"公司")
                    c.execute("INSERT INTO jobs(id,tenant_id,owner_id,owner_version,node_id,kind,question,input_level,state,stage,fingerprint) VALUES(%s,%s,%s,%s,%s,'ask',%s,3,'legacy','历史记录待重新核验',%s) ON CONFLICT(id) DO NOTHING",(r["id"],tenant,user["id"],user["version"],root,task.get("question",""),hashlib.sha256(r["task_json"].encode()).hexdigest()))
    print("旧账号、资料权限与任务档案已迁移；旧会话未迁移")
def bootstrap_owner():
    with database_connection() as c:
        for tenant in fetch_all(c,"SELECT * FROM tenants"):
            if fetch_one(c,"SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=%s AND m.role='boss' AND m.active AND u.active",(tenant["id"],)):continue
            password=secrets.token_urlsafe(20);user=generate_id("user")
            c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,must_change) VALUES(%s,%s,'owner','公司老板',%s,true)",(user,tenant["id"],hash_password(password,"production")))
            c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,'boss')",(tenant["id"],user,node_id(tenant["id"],"公司")))
            path=STATE/("bootstrap-owner-"+tenant["code"]+".json")
            path.write_text(json.dumps(dict(username="owner",password=password,user_id=user,tenant_code=tenant["code"]),ensure_ascii=False));path.chmod(0o600)
            c.execute("INSERT INTO audit_events(tenant_id,actor_id,action,target_id,system) VALUES(%s,%s,'受保护工具初始化老板身份',%s,true)",(tenant["id"],user,user))
    print("首次老板身份已初始化，凭据仅保存在部署账号私有目录")
def migrate_task_list():
    # 无法确认旧所有者的记录只交由本企业老板核验，不能猜测归属或公开旧答案。
    with database_connection() as c:
        for archived in fetch_all(c,"SELECT * FROM legacy_archive WHERE source='tasks.sqlite:tasks'"):
            tenant=archived["tenant_id"];record=archived["record"]
            task=json.loads(record.get("task_json") or "{}")
            owner=fetch_one(c,"SELECT id,version FROM users WHERE tenant_id=%s AND id=%s",(tenant,task.get("user_id","")))
            if not owner:
                owner=fetch_one(c,"SELECT u.id,u.version FROM users u JOIN memberships m ON m.user_id=u.id AND m.tenant_id=u.tenant_id WHERE u.tenant_id=%s AND u.active AND m.active AND m.role='boss' ORDER BY u.created_at LIMIT 1",(tenant,))
            if not owner:continue
            c.execute("INSERT INTO jobs(id,tenant_id,owner_id,owner_version,node_id,kind,question,input_level,state,stage,fingerprint,payload) VALUES(%s,%s,%s,%s,%s,'ask',%s,3,'legacy','历史记录待重新核验',%s,%s) ON CONFLICT(id) DO NOTHING",(record["id"],tenant,owner["id"],owner["version"],node_id(tenant,"公司"),task.get("question",""),hashlib.sha256(record.get("task_json","").encode()).hexdigest(),as_jsonb({"legacy_source_id":archived["source_id"]})))
    print("历史任务列表已恢复；旧答案保持受限并待重新核验")
def provision():
    import psycopg
    from psycopg import sql
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700);(STATE/"socket").mkdir(exist_ok=True,mode=0o700)
    private=STATE/"database-secrets.json"
    if private.exists():secrets_data=json.loads(private.read_text())
    else:
        secrets_data={name:secrets.token_urlsafe(32) for name in ("owner","demo","production","test")}
        private.write_text(json.dumps(secrets_data));private.chmod(0o600)
    if "test" not in secrets_data:
        secrets_data["test"]=secrets.token_urlsafe(32)
        private.write_text(json.dumps(secrets_data));private.chmod(0o600)
    data=STATE/"postgres";bin=RUNTIME/"postgres/bin"
    pw=STATE/"initdb-password";pw.write_text(secrets_data["owner"]);pw.chmod(0o600)
    if not (data/"PG_VERSION").exists():
        subprocess.run([str(bin/"initdb"),"-D",str(data),"--auth-local=peer","--auth-host=scram-sha-256","--username",os.environ.get("USER") or __import__("getpass").getuser(),"--pwfile",str(pw),"--locale=C.UTF-8"],check=True,stdout=subprocess.DEVNULL)
        with (data/"postgresql.conf").open("a") as out:out.write("\nlisten_addresses='127.0.0.1'\nport=15432\nunix_socket_directories='"+str(STATE/"socket")+"'\n")
    pw.unlink(missing_ok=True)
    status=subprocess.run([str(bin/"pg_ctl"),"-D",str(data),"status"],stdout=subprocess.DEVNULL)
    if status.returncode:
        subprocess.run([str(bin/"pg_ctl"),"-D",str(data),"-l",str(STATE/"postgres.log"),"start","-w"],check=True,stdout=subprocess.DEVNULL)
    with psycopg.connect(host=str(STATE/"socket"),port=15432,dbname="postgres",autocommit=True) as c:
        for mode in ("demo","production","test"):
            role="ecom_"+mode;dbname="ecom_"+mode
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(role,)).fetchone():c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(secrets_data[mode])))
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s",(dbname,)).fetchone():c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(dbname),sql.Identifier(role)))
            c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
            env=ROOT/(".env."+mode)
            if not env.exists():
                env.write_text("ECOM_MODE="+mode+"\nECOM_DATABASE_URL=postgresql://"+role+":"+secrets_data[mode]+"@127.0.0.1:15432/"+dbname+"\nECOM_TENANT_CODE=mofa\nECOM_COOKIE_SECURE=false\n")
                env.chmod(0o600)
            subprocess.run([str(ROOT/".venv/bin/python"),"-m","alembic","-c","alembic.ini","upgrade","head"],cwd=ROOT,env={**os.environ,"ECOM_ENV_FILE":str(env),"PYTHONPATH":str(ROOT/"src")},check=True)
    print("私有 PostgreSQL 演示库、正式库和独立测试库已就绪")
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="企业数据库初始化、演示种子和历史迁移管理")
    parser.add_argument("command", choices=("provision", "demo", "migrate", "bootstrap-owner", "archive-tasks"))
    args = parser.parse_args()
    {"provision": provision, "demo": seed_demo, "migrate": migrate, "bootstrap-owner": bootstrap_owner, "archive-tasks": migrate_task_list}[args.command]()
