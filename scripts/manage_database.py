"""企业数据库初始化（迁移建表）、测试夹具建库与企业账号准备。"""
import os,sys,json,secrets,subprocess,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from taozhi.config import load_config, resolve_env_file, resolve_runtime_dir
from taozhi.infrastructure.persistence.database import database_connection,fetch_one,fetch_all,as_jsonb
from taozhi.infrastructure.persistence.job_repo import generate_id
from taozhi.domain.passwords import hash_password
from taozhi.infrastructure.knowledge.ingest import split
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
TEMPLATE=[
 ("商品产品部",["商品资料组","产品适配组"]),("电商运营部",["店铺运营组","内容营销组"]),
 ("客服售后部",["售前客服组","售后服务组"]),("采购供应链部",["采购组","仓储物流组"]),
 ("财务综合部",["财务组","行政组"])]
def node_id(tenant,name):
    """按租户与节点名称生成稳定的组织节点标识。

    Args:
        tenant: 租户标识。
        name: 组织节点名称。

    Returns:
        稳定的组织节点标识。
    """
    return "org_"+hashlib.sha256((tenant+":"+name).encode()).hexdigest()[:24]
def organization(c,tenant,name):
    """为公司建立部门与小组两级组织架构，并返回名称到节点的映射。

    Args:
        c: 数据库连接。
        tenant: 租户标识。
        name: 公司根节点名称。

    Returns:
        组织节点名称到标识的映射。
    """
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
    """写入一篇文档及其版本、切片与提及索引。

    Args:
        c: 数据库连接。
        tenant: 租户标识。
        creator: 创建人标识。
        did: 文档标识。
        title: 文档标题。
        body: 文档正文。
        node: 归属组织节点。
        scope: 可见范围。
        level: 密级。
        state: 文档状态。
        version: 版本号。
    """
    c.execute("INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,download_allowed,state,current_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",(did,tenant,node,creator,title,scope,level,level<3,level==1,state,version))
    c.execute("INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,created_by,policy) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(tenant,did,version,title,body,hashlib.sha256(body.encode()).hexdigest(),creator,as_jsonb(dict(node_id=node,scope=scope,level=level))))
    for ch in split([(0,body)],did,version):
        c.execute("INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(ch["id"],tenant,did,version,ch["ordinal"],ch["page"],ch["text"]))
    from taozhi.infrastructure.persistence.document_repo import index_mentions
    index_mentions(c,tenant,did,version,split([(0,body)],did,version))
def corpus():
    """读取 data/index/chunks.json 并按文档分组。

    Returns:
        文档标识到切片列表的映射。
    """
    path=ROOT/"data/index/chunks.json"
    groups={}
    for chunk in json.loads(path.read_text()) if path.exists() else []:
        groups.setdefault(chunk["doc_id"],[]).append(chunk)
    return groups
def seed_test():
    """初始化测试库：企业租户、四种身份账号与密级样文档（仅 test 模式，供在线验收使用）。"""
    from taozhi.config import load_config
    if load_config().mode!="test":
        raise RuntimeError("测试初始化仅允许测试环境")
    with database_connection() as c:
        tenant="tenant_test"
        c.execute("INSERT INTO tenants(id,code,name,mode) VALUES(%s,'mofa','手机配件电商 · 演示企业','test') ON CONFLICT(id) DO NOTHING",(tenant,))
        nodes=organization(c,tenant,"手机配件电商 · 演示企业")
        users={}
        hashed=hash_password("123456","test")
        for username,display,admin in [("admin","系统管理员",True),("staff","运营员工",False),("leader","运营组长",False),("boss","公司老板",False)]:
            existing=fetch_one(c,"SELECT id FROM users WHERE tenant_id=%s AND username=%s",(tenant,username))
            user=existing["id"] if existing else generate_id("user");users[username]=user
            c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,system_admin,must_change) VALUES(%s,%s,%s,%s,%s,%s,false) ON CONFLICT(tenant_id,username) DO NOTHING",(user,tenant,username,display,hashed,admin))
        for user,node,role in [(users["staff"],nodes["店铺运营组"],"employee"),(users["leader"],nodes["店铺运营组"],"leader"),(users["boss"],nodes["公司"],"boss")]:
            c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(tenant,user,node,role))
        for did,chunks in corpus().items():
            add_doc(c,tenant,users["boss"],"test_"+did,chunks[0].get("doc_name","商品资料"),"\n".join(x["text"] for x in chunks),nodes["公司"])
        samples=[
          ("company","企业数据安全与文档密级分级规范","全员遵守企业信息安全准则。员工依岗位授权读取已获准商品资料；组长负责本部门业务审核与文档初审；老板审批跨部门及三级机密授权；管理员负责系统底层与账号运维。","公司","company",1),
          ("team","店铺运营组日常业务作业指导书","店铺运营组负责电商各渠道商品详情维护、上架参数核验与客户售前咨询支持。日常作业必须严格引用官方核准规格，杜绝臆测适配型号或夸大售后承诺。","店铺运营组","org",1),
          ("leader","电商运营组工作职责与内容发布审核规范","运营组长全面负责本组日常运营内容与上架文档的二级合规复核。遇跨部门供应链或涉保修例外事项，应发起跨部门协同审批。","店铺运营组","org",2),
          ("procurement","供应链采购协同与供应商日常管理指引","采购组负责品牌官方配件及核心耗材供应商对接、准入资质审核与出厂批次抽检。非本组经办人员查阅进货明细及供应商底价需单独授权。","采购组","org",2),
          ("boss","企业经营战略与核心商业秘密保护规定","公司核心经营指标、战略采购底价协议及关键供应商股权合作事宜归属三级核心商业秘密，由企业法定代表人统一管理，严格禁止向未经审核的第三方外部模型出境传输。","公司","company",3)]
        for tag,title,body,node,scope,level in samples:add_doc(c,tenant,users["boss"],"test_doc_"+tag,title,body,nodes[node],scope,level)
    print("演示企业与四种现有正式账号已初始化")

def bootstrap_owner():
    """为缺少老板的租户创建 owner 账号，并把一次性口令写入运行时目录。"""
    with database_connection() as c:
        for tenant in fetch_all(c,"SELECT * FROM tenants"):
            if fetch_one(c,"SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=%s AND m.role='boss' AND m.active AND u.active",(tenant["id"],)):continue
            password=secrets.token_urlsafe(20);user=generate_id("user")
            c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,must_change) VALUES(%s,%s,'owner','公司老板',%s,true)",(user,tenant["id"],hash_password(password,"production")))
            c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,'boss')",(tenant["id"],user,node_id(tenant["id"],"公司")))
            path=resolve_runtime_dir()/("bootstrap-owner-"+tenant["code"]+".json")
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            path.write_text(json.dumps(dict(username="owner",password=password,user_id=user,tenant_code=tenant["code"]),ensure_ascii=False));path.chmod(0o600)
            c.execute("INSERT INTO audit_events(tenant_id,actor_id,action,target_id,system) VALUES(%s,%s,'受保护工具初始化老板身份',%s,true)",(tenant["id"],user,user))
    print("首次老板身份已初始化，凭据已写入运行时目录")


def _load_cfg_for_env(env_path: Path):
    """在临时切换 ECOM_ENV_FILE 的情况下加载配置。

    Args:
        env_path: 环境配置文件路径。

    Returns:
        解析后的配置对象。
    """
    prev = os.environ.get("ECOM_ENV_FILE")
    os.environ["ECOM_ENV_FILE"] = str(env_path)
    try:
        return load_config()
    finally:
        if prev is None:
            os.environ.pop("ECOM_ENV_FILE", None)
        else:
            os.environ["ECOM_ENV_FILE"] = prev


def provision():
    """针对已运行的 PostgreSQL（如 Docker postgres 服务）执行建库（如权限允许）与 Alembic 迁移。"""
    env_files = [Path(os.environ["ECOM_ENV_FILE"])] if os.environ.get("ECOM_ENV_FILE") else [
        resolve_env_file(os.environ.get("ECOM_MODE", "production"))
    ]
    if not env_files[0].is_file():
        raise RuntimeError(f"未找到环境配置文件：{env_files[0]}（请检查 .env 或设置 ECOM_ENV_FILE）")
    for env_path in env_files:
        env = {**os.environ, "ECOM_ENV_FILE": str(env_path), "PYTHONPATH": str(ROOT / "src")}
        cfg = _load_cfg_for_env(env_path)
        parts = conninfo_to_dict(cfg.dsn)
        dbname = parts.get("dbname", "")
        admin_dsn = os.environ.get("ECOM_PG_ADMIN_DSN") or make_conninfo(**{**parts, "dbname": "postgres"})
        try:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                if dbname and not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)).fetchone():
                    admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(dbname), sql.Identifier(parts.get("user", "postgres"))
                    ))
                    admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
        except psycopg.Error:
            pass
        with psycopg.connect(cfg.dsn, autocommit=True) as target:
            target.execute("CREATE EXTENSION IF NOT EXISTS vector")
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=ROOT, env=env, check=True,
        )
    print("PostgreSQL 数据库结构与迁移已就绪")
def quick_passwords():
    """内网快捷登录：把本企业全部在用账号口令统一设为 ECOM_QUICK_LOGIN_PASSWORD。

    仅在 ECOM_QUICK_LOGIN=true 且已配置 ECOM_QUICK_LOGIN_PASSWORD 时执行；该口令为弱口令，
    仅供内网演示与验收，上线公网前必须重新为每个账号设置强口令。
    """
    from taozhi.config import load_config
    cfg=load_config()
    if not cfg.quick_login or not cfg.quick_login_password:
        raise SystemExit("需同时开启 ECOM_QUICK_LOGIN 并配置 ECOM_QUICK_LOGIN_PASSWORD")
    hashed=hash_password(cfg.quick_login_password,cfg.mode,allow_weak=True)
    with database_connection() as c:
        tenant=fetch_one(c,"SELECT id FROM tenants WHERE code=%s",(cfg.tenant_code,))
        if not tenant:raise SystemExit("未找到企业租户 code="+cfg.tenant_code)
        rows=fetch_all(c,"SELECT username FROM users WHERE tenant_id=%s AND active ORDER BY username",(tenant["id"],))
        for r in rows:
            c.execute("UPDATE users SET password_hash=%s,must_change=false WHERE tenant_id=%s AND username=%s",(hashed,tenant["id"],r["username"]))
            print("  已重置快捷登录口令："+r["username"])
    print("快捷登录默认口令已生效，共 "+str(len(rows))+" 个在用账号")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="企业数据库初始化与账号准备")
    parser.add_argument("command", choices=("provision", "bootstrap-owner", "quick-passwords"))
    args = parser.parse_args()
    {"provision": provision, "bootstrap-owner": bootstrap_owner, "quick-passwords": quick_passwords}[args.command]()
