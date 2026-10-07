"""真实浏览器验证四种身份、中文页面与一次真实模型问答。"""
from pathlib import Path
import json,time,re,os
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/"data/runtime/browser"
out.mkdir(parents=True,exist_ok=True,mode=0o700)
base=os.environ.get("ECOM_BROWSER_URL","http://127.0.0.1:18502")
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
 summary=[]
 for role in ("admin","staff","leader","boss"):
  context=browser.new_context(locale="zh-CN",viewport={"width":1440,"height":1000})
  page=context.new_page();errors=[]
  page.on("pageerror",lambda e:errors.append(str(e)))
  page.goto(base,wait_until="networkidle")
  assert page.locator("html").get_attribute("lang")=="zh-CN"
  page.get_by_placeholder("请输入账号").fill(role)
  page.get_by_placeholder("请输入密码").fill("123456")
  page.get_by_role("button",name="登录",exact=True).click()
  page.get_by_role("heading",name="把知识与协作，放在同一个工作台").wait_for(timeout=30000)
  page.wait_for_timeout(500)
  data=context.request.get(base+"/api/v2/documents").json()
  visible={d["id"] for d in data["items"]}
  if role in ("admin","staff","leader"):assert "demo_doc_boss" not in visible
  if role=="staff":assert "demo_doc_leader" not in visible
  if role=="leader":assert "demo_doc_leader" in visible and "demo_doc_procurement" not in visible
  if role=="boss":assert "demo_doc_boss" in visible
  assert ("组织与人员" in page.locator("nav").inner_text())==(role in ("admin","boss"))
  if role=="staff" and os.environ.get("ECOM_BROWSER_SKIP_MODEL")!="true":
   page.locator("nav button",has_text="智能问答").click()
   page.get_by_placeholder("请说明商品型号、适配机型或需要核对的业务事项").fill("MC-500 的切幅是多少？")
   page.get_by_role("button",name="提交问题",exact=True).click()
   page.get_by_text("任务详情",exact=True).wait_for()
   deadline=time.time()+150;result=None
   while time.time()<deadline:
    listing=context.request.get(base+"/api/v2/tasks").json()["items"]
    if listing:
     job=context.request.get(base+"/api/v2/tasks/"+listing[0]["id"]).json()
     if job["state"] in ("completed","failed"):result=job;break
    page.wait_for_timeout(2000)
   assert result and result["state"]=="completed",result
   assert result["result"]["model_calls"]>=1
   assert any(x["outcome"]=="ok" for x in result["result"]["usage"]),result["result"]["usage"]
   assert result["result"]["citations"]
   page.wait_for_timeout(3000)
   page.screenshot(path=str(out/"真实问答.png"),full_page=True)
   page.get_by_role("button",name="关闭此对话框").first.click()
  for menu in ("文档库","任务中心","个人设置"):
   page.locator("nav button",has_text=menu).click();page.wait_for_timeout(350)
   assert page.locator(".page-heading").inner_text()
  if role in ("admin","boss"):
   page.locator("nav button",has_text="组织与人员").click();page.wait_for_timeout(350)
   assert "采购供应链部" in page.locator(".content").inner_text()
  page.locator("nav button",has_text="文档库").click();page.wait_for_timeout(500)
  page.screenshot(path=str(out/(role+"-文档库.png")),full_page=True)
  assert not errors,errors
  summary.append({"身份":role,"可读资料":len(visible),"页面错误":len(errors)})
  context.close()
 # 同一浏览器退出老板后再登录员工，避免弹窗、答案和表单残留。
 warm=browser.new_context(locale="zh-CN",viewport={"width":1440,"height":1000})
 page=warm.new_page();page.goto(base,wait_until="networkidle")
 page.get_by_placeholder("请输入账号").fill("boss")
 page.get_by_placeholder("请输入密码").fill("123456")
 page.get_by_role("button",name="登录",exact=True).click()
 page.get_by_role("heading",name="把知识与协作，放在同一个工作台").wait_for()
 page.locator("nav button",has_text="文档库").click()
 page.get_by_text("老板经营决策备忘录",exact=True).first.click()
 page.get_by_text("老板级机密权限演示。",exact=False).wait_for()
 page.get_by_role("button",name="关闭此对话框").first.click()
 page.get_by_role("button",name="退出登录",exact=True).click()
 page.get_by_placeholder("请输入账号").wait_for()
 page.get_by_placeholder("请输入账号").fill("staff")
 page.get_by_placeholder("请输入密码").fill("123456")
 page.get_by_role("button",name="登录",exact=True).click()
 page.get_by_role("heading",name="把知识与协作，放在同一个工作台").wait_for()
 assert "老板级机密权限演示。" not in page.locator("body").inner_text()
 page.reload(wait_until="networkidle")
 page.get_by_role("heading",name="把知识与协作，放在同一个工作台").wait_for()
 assert "老板经营决策备忘录" not in page.locator("body").inner_text()
 summary.append({"身份":"同浏览器老板退出后登录员工","会话残留":False})
 warm.close()
 browser.close()
(out/"验收结果.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
