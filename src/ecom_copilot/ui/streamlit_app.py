"""中文企业工作台：账号登录、角色菜单和后端授权，不暴露令牌输入。"""
from __future__ import annotations
import os,json,datetime
import httpx
import streamlit as st
st.set_page_config(page_title="手机配件企业工作台",layout="wide")
BASE=os.getenv("ECOM_API_URL","http://127.0.0.1:18080").rstrip("/")
ROLES={"admin":"管理员","boss":"老板","employee":"员工"}
STATUS={"pending":"排队中","processing":"处理中","awaiting_review":"待审核","completed":"已完成",
        "failed":"处理失败","cancelled":"已取消","rejected":"已拒绝"}
VIS={"management":"仅管理员和老板","company":"全体员工可读","selected":"指定人员可读"}
STAGES={"queued":"任务排队","start":"开始处理","data_collection":"资料采集","doc_parsing":"文档解析",
        "knowledge_extraction":"知识抽取","graph_building":"知识关系整理","deep_research":"深入核验",
        "specialist_review":"专家协作核验","report_generation":"生成答案","quality_assessment":"发布质量检查",
        "__interrupt__":"等待审核","quality_gate":"发布审核","done":"本轮完成","review":"人工审核","terminal":"任务结束"}
st.markdown("""<style>
[data-testid="stFileUploaderDropzoneInstructions"] span {font-size:0}
[data-testid="stFileUploaderDropzoneInstructions"] span::after {content:"拖放文件到这里";font-size:14px}
[data-testid="stFileUploaderDropzoneInstructions"] small {font-size:0}
[data-testid="stFileUploaderDropzoneInstructions"] small::after {content:"支持 PDF、TXT、Markdown、DOCX，最大 20 MB";font-size:12px}
[data-testid="stFileUploaderDropzone"] button {font-size:0}
[data-testid="stFileUploaderDropzone"] button::after {content:"选择文件";font-size:14px}
</style>""",unsafe_allow_html=True)
def request(method,path,**kwargs):
    try:
        token=st.session_state.get("token","")
        with httpx.Client(trust_env=False,timeout=20) as c:
            r=c.request(method,BASE+path,headers={"Authorization":"Bearer "+token} if token else {},**kwargs)
        if r.status_code>=400:
            detail=r.json().get("detail","操作未完成")
            if r.status_code==401 and token:
                st.session_state.clear();st.session_state["flash"]="登录已过期或权限已变更，请重新登录";st.rerun()
            st.error(detail if isinstance(detail,str) else "输入格式不正确，请检查填写内容")
            return None
        return r.json()
    except httpx.HTTPError:
        st.error("服务暂时无法连接，请检查工作台连接或联系管理员")
        return None
def flash(message):
    st.session_state["flash"]=message;st.rerun()
def logout(message="已退出登录"):
    request("POST","/api/auth/logout")
    st.session_state.clear();st.session_state["flash"]=message;st.rerun()
def password_form(prefix):
    with st.form(prefix,clear_on_submit=True):
        current=st.text_input("原密码",type="password")
        new=st.text_input("新密码（至少 12 个字符）",type="password")
        repeat=st.text_input("再次输入新密码",type="password")
        sent=st.form_submit_button("修改密码")
    if sent:
        if new!=repeat:st.error("两次新密码不一致")
        elif len(new)<12:st.error("新密码至少 12 个字符")
        elif request("POST","/api/auth/password",json={"current_password":current,"new_password":new}):
            st.session_state.clear();st.session_state["flash"]="密码已修改，请重新登录";st.rerun()

st.title("手机配件企业工作台")
st.caption("商品问答 · 企业文档 · 权限管理 · 多智能体协作")
if st.session_state.get("flash"):st.info(st.session_state.pop("flash"))
if not st.session_state.get("token"):
    st.subheader("账号登录")
    with st.form("login",clear_on_submit=True):
        username=st.text_input("账号")
        password=st.text_input("密码",type="password")
        sent=st.form_submit_button("登录",type="primary")
    if sent:
        out=request("POST","/api/auth/login",json={"username":username,"password":password})
        if out:
            st.session_state["token"]=out["access_token"]
            st.session_state["user"]=out["user"]
            st.rerun()
    st.caption("账号由本企业管理员或老板创建。忘记密码请联系管理员重置。")
    st.stop()
user=request("GET","/api/auth/me")
if not user:st.stop()
st.session_state["user"]=user
perms=set(user["permissions"]);manager="knowledge:write" in perms
with st.sidebar:
    st.subheader(user["display_name"] or user["username"])
    st.write("企业："+user["org_tag"])
    st.write("角色："+"、".join(ROLES.get(r,r) for r in user["roles"]))
    st.caption("员工仅能读取获授权资料；隐私文档由管理层设置。")
    if st.button("退出登录"):logout()
if user["must_change_password"]:
    st.warning("首次登录须修改临时密码。修改完成后才能使用工作台。")
    password_form("initial-password")
    st.stop()
names=["商品问答","我的任务","文档库"]
if manager:names+=["审核中心","账号管理","操作审计"]
names+=["我的账号"]
tabs=dict(zip(names,st.tabs(names)))

def show_report(out,prefix="ask"):
    result=out.get("result") or {};report=result.get("report") or {}
    st.write("状态："+STATUS.get(out["status"],out["status"]))
    if result.get("publication")=="permission_changed":st.warning(result["message"]);return
    if out["status"]=="awaiting_review":
        st.warning("答案尚未发布，等待管理员或老板审核。")
        if not manager:return
    if report and (out["status"] in ("completed","awaiting_review")):
        st.subheader(report.get("title","商品答案"))
        st.markdown(report.get("executive_summary",""))
        for section in report.get("sections",[]):
            st.subheader(section["heading"]);st.markdown(section["content"])
        warnings=report.get("risk_warnings") or []
        if warnings:
            with st.expander("注意事项"):
                for warning in warnings:st.write("• "+warning)
        with st.expander("原文引用"):
            for c in report.get("citations",[]):
                st.write(f"【{c.get('index',0)}】{c.get('doc_name','资料')}"+(f" · 第 {c['page']} 页" if c.get("page") else ""))
                st.caption(c.get("quote",""))
        text=report.get("executive_summary","")+"\n\n"+"\n\n".join(s["heading"]+"\n"+s["content"] for s in report.get("sections",[]))
        st.download_button("下载答案",text,file_name="商品答案.txt",key="download-"+prefix+"-"+out["task_id"])
    elif out["status"]=="failed":st.error("任务未完成，请稍后重试或联系管理员。")
    elif out["status"]=="rejected":st.warning("该答案被审核拒绝，请补充资料后重新提问。")
def table_tasks(items):
    return [{"任务编号":i["task_id"],"问题":i["question"],"状态":STATUS.get(i["status"],i["status"]),
             "提交时间":datetime.datetime.fromtimestamp(i["created_at"],datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")} for i in items]

with tabs["商品问答"]:
    with st.form("question"):
        question=st.text_area("你想了解什么？",placeholder="例如：C3 防窥膜支持哪些手机型号？")
        a,b=st.columns(2)
        category=a.text_input("品类",value="手机配件")
        depth=b.selectbox("核验深度",["quick","standard","deep"],format_func=lambda x:{"quick":"快速核验","standard":"标准核验","deep":"深度核验"}[x])
        with st.expander("业务查询选项"):
            sku=st.text_input("商品编号（查询实时库存或价格时填写）")
            order=st.text_input("订单编号（查询订单时填写）")
        force=st.checkbox("请求人工审核")
        sent=st.form_submit_button("提问",type="primary")
    if sent:
        if not question.strip():st.warning("请输入问题")
        else:
            out=request("POST","/api/research/task",json={"question":question,"category":category,"depth":depth,
                "sku_id":sku,"order_id":order,"require_human_review":force})
            if out:st.session_state["task_id"]=out["task_id"]
    task_id=st.session_state.get("task_id")
    if task_id:
        st.button("刷新问答进度")
        out=request("GET","/api/research/task/"+task_id)
        if out:
            show_report(out)
            with st.expander("协作进度"):
                for event in out.get("events",[]):
                    if event.get("stage") in STAGES:st.write(STAGES[event["stage"]])
            if out["status"] in ("pending","processing") and st.button("取消本次查询"):
                request("POST","/api/research/task/"+task_id+"/cancel");st.rerun()
with tabs["我的任务"]:
    out=request("GET","/api/research/tasks")
    items=(out or []) if not isinstance(out,dict) else out.get("tasks",[])
    mine=[i for i in items if i.get("task_id")]
    if mine:
        st.dataframe(table_tasks(mine),hide_index=True,width="stretch")
        tid=st.selectbox("查看任务",[""]+[i["task_id"] for i in mine],
                         format_func=lambda x:next((i["question"] for i in mine if i["task_id"]==x),"请选择"))
        if tid:
            detail=request("GET","/api/research/task/"+tid)
            if detail:show_report(detail,"history")
    else:st.info("暂无可查看任务")
with tabs["文档库"]:
    query=st.text_input("检索获授权的商品资料")
    if query and st.button("搜索资料"):
        found=request("POST","/api/knowledge/search",json={"query":query})
        for hit in (found or {}).get("hits",[]):
            st.write(hit["doc_name"]);st.caption(hit["text"])
    result=request("GET","/api/knowledge/documents")
    docs=(result or {}).get("documents",[])
    st.caption(f"当前可查看 {len(docs)} 份文档。目录和正文均受权限限制。")
    if docs:
        doc_id=st.selectbox("选择文档",[""]+[d["id"] for d in docs],
             format_func=lambda x:next((d["title"]+" · "+VIS[d["visibility"]] for d in docs if d["id"]==x),"请选择"))
        if doc_id:
            doc=request("GET","/api/knowledge/documents/"+doc_id)
            if doc:
                st.subheader(doc["title"])
                st.text_area("文档正文",doc["text"],height=260,disabled=True)
                st.download_button("下载可读正文",doc["text"],file_name="企业资料.txt")
                if manager:
                    people=(request("GET","/api/users") or {}).get("users",[])
                    options={p["id"]:p["display_name"]+"（"+ROLES[p["role"]]+"）" for p in people if p["active"]}
                    with st.form("doc-permissions"):
                        visibility=st.selectbox("文档可见范围",list(VIS),index=list(VIS).index(doc["visibility"]),format_func=VIS.get)
                        allowed=st.multiselect("指定可读人员",list(options),default=[x for x in doc["allowed_users"] if x in options],format_func=options.get)
                        change=st.form_submit_button("保存文档权限")
                    if change:
                        if request("PATCH","/api/knowledge/documents/"+doc_id+"/permissions",json={"visibility":visibility,"allowed_users":allowed}):
                            flash("文档权限已更新，后续读取与检索立即生效")
                    with st.expander("编辑文档"):
                        with st.form("edit-document"):
                            title=st.text_input("文档标题",doc["title"])
                            body=st.text_area("修改正文",doc["text"],height=240)
                            edit=st.form_submit_button("保存修改")
                        if edit and request("PATCH","/api/knowledge/documents/"+doc_id,json={"title":title,"text":body}):flash("文档已更新")
                    confirm=st.checkbox("确认删除所选文档")
                    if st.button("删除文档",disabled=not confirm):
                        if request("DELETE","/api/knowledge/documents/"+doc_id):flash("文档已删除")
    if manager:
        st.divider();st.subheader("新增资料")
        st.caption("新文档默认仅管理员和老板可见，入库后可调整授权。")
        file=st.file_uploader("上传企业资料",type=["pdf","txt","md","docx"])
        if file and st.button("上传并入库"):
            out=request("POST","/api/knowledge/upload",files={"file":(file.name,file.getvalue(),file.type)})
            if out:flash("资料已入库，当前仅管理层可见")
        with st.expander("直接录入文本"):
            with st.form("add-document"):
                title=st.text_input("新文档标题")
                text=st.text_area("新文档正文")
                create=st.form_submit_button("保存文档")
            if create and request("POST","/api/knowledge/documents",json={"title":title,"text":text}):flash("文档已保存，当前仅管理层可见")

if manager:
    with tabs["审核中心"]:
        records=request("GET","/api/research/tasks")
        pending=[i for i in (records or []) if i["status"]=="awaiting_review"]
        if not pending:st.info("暂无待审核任务")
        else:
            chosen=st.selectbox("待审核任务",[i["task_id"] for i in pending],
                format_func=lambda x:next(i["question"] for i in pending if i["task_id"]==x))
            detail=request("GET","/api/research/task/"+chosen)
            if detail:
                show_report(detail,"review")
                quality=(detail.get("result") or {}).get("quality") or {}
                for reason in quality.get("reasons",[]):st.warning(reason)
            comment=st.text_area("审核意见")
            a,b=st.columns(2)
            if a.button("批准发布"):
                if request("POST","/api/research/task/"+chosen+"/review",json={"decision":"approve","comment":comment}):flash("已批准发布")
            if b.button("拒绝发布"):
                if request("POST","/api/research/task/"+chosen+"/review",json={"decision":"reject","comment":comment}):flash("已拒绝发布")
    with tabs["账号管理"]:
        people=(request("GET","/api/users") or {}).get("users",[])
        st.dataframe([{"账号":p["username"],"姓名":p["display_name"],"角色":ROLES[p["role"]],"状态":"启用" if p["active"] else "停用",
                       "首次改密":"需要" if p["must_change"] else "已完成"} for p in people],hide_index=True)
        with st.expander("创建账号"):
            with st.form("new-user",clear_on_submit=True):
                username=st.text_input("新账号")
                name=st.text_input("姓名或显示名称")
                role=st.selectbox("账号角色",["employee","admin","boss"],format_func=ROLES.get)
                password=st.text_input("临时密码（至少 12 个字符）",type="password")
                sent=st.form_submit_button("创建账号")
            if sent and request("POST","/api/users",json={"username":username,"display_name":name,"role":role,"password":password}):flash("账号已创建，首次登录须修改密码")
        if people:
            uid=st.selectbox("管理人员",[p["id"] for p in people],format_func=lambda x:next(p["display_name"] for p in people if p["id"]==x))
            person=next(p for p in people if p["id"]==uid)
            with st.form("edit-user"):
                role=st.selectbox("调整角色",list(ROLES),index=list(ROLES).index(person["role"]),format_func=ROLES.get)
                active=st.checkbox("允许登录",value=bool(person["active"]))
                name=st.text_input("显示名称",person["display_name"])
                new=st.text_input("重置临时密码（留空则保持）",type="password")
                sent=st.form_submit_button("保存账号设置")
            if sent:
                payload={"role":role,"active":active,"display_name":name}
                if new:payload["new_password"]=new
                if request("PATCH","/api/users/"+uid,json=payload):flash("账号已更新，旧登录会话已失效")
    with tabs["操作审计"]:
        events=(request("GET","/api/audit") or {}).get("events",[])
        st.dataframe([{"时间":datetime.datetime.fromtimestamp(e["created_at"],datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
                       "操作":e["action"],"操作人":e["actor"],"对象":e["target"]} for e in events],hide_index=True)
with tabs["我的账号"]:
    st.write("账号："+user["username"])
    st.write("企业："+user["org_tag"])
    st.write("角色："+"、".join(ROLES.get(r,r) for r in user["roles"]))
    password_form("own-password")
