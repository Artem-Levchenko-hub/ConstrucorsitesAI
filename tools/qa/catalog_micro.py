import os,json,pathlib
from playwright.sync_api import sync_playwright
O="https://yleum.ru";D=pathlib.Path("/tmp/qa-public");D.mkdir(exist_ok=True,mode=448);R={"id":"Т01.2","accepted":False,"checks":[]}
with sync_playwright() as p:
 b=p.chromium.launch(args=["--no-sandbox"],proxy={"server":os.environ["HTTPS_PROXY"]} if os.getenv("HTTPS_PROXY") else None);c=b.new_context(ignore_https_errors=False);q=c.new_page()
 c.route("**/*",lambda r:r.continue_() if (r.request.method in ["GET","HEAD","OPTIONS"] and ("/api/" not in r.request.url or any(x in r.request.url for x in ["/api/auth/oauth/providers","/api/max/account/access"]))) or (r.request.method=="POST" and r.request.url==O+"/login") else r.abort())
 try:
  for a in ["/pricing","/max/start","/login"]:
   r=q.goto(O+a);assert r.status==200;h=q.locator("h1");assert h.count();R["checks"].append([a,200]);q.screenshot(path=str(D/(a[1:].replace("/","-")+".png")),mask=[q.locator("input")])
  r=c.request.get(O+"/api/auth/oauth/providers");assert r.status==200;v=r.json()["providers"];R["providers"]=[{k:x[k] for k in ["provider","label"]} for x in v];assert q.locator("[data-oauth-provider]").count()==len(v)
  if os.getenv("QA_LOGIN"):
   q.locator("#email").fill(os.environ["QA_LOGIN"]);q.locator("#password").fill(os.environ["QA_PASSWORD"]);q.get_by_role("button",name="Войти",exact=True).click();q.wait_for_url(lambda u:"/login" not in u)
   c.route("**/*",lambda r:r.abort() if r.request.method not in ["GET","HEAD","OPTIONS"] else r.fallback())
   q.goto(O+"/max");q.get_by_role("button",name="Создать приложение",exact=True).first.click();d=q.get_by_role("dialog");d.wait_for();v=d.locator("fieldset button");assert v.count()>0;R["types"]=v.all_inner_texts();d.screenshot(path=str(D/"wizard.png"));q.keyboard.press("Escape")
  R["status"]="PARTIAL"
 except Exception as e:R["status"]="BLOCKED";R["errorType"]=type(e).__name__
 finally:b.close()
(D/"result.json").write_text(json.dumps(R,ensure_ascii=False));print(json.dumps(R,ensure_ascii=False))
