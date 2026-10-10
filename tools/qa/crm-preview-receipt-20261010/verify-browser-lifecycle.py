import json,threading,http.server
from pathlib import Path
from playwright.sync_api import sync_playwright
# Local-only real-browser probe of the actual candidate storage/lock helpers.
# No MAX identity is forged and no external SDK/provider request is performed.
import subprocess
repo = Path(__file__).resolve().parents[3]
source = (Path(__file__).parent / "after/src/app/page.tsx").read_text()
helpers_ts = source[source.index("const UUID_KEY"):source.index("export default function App()")]
transpile = "const ts=require('typescript'),fs=require('fs');process.stdout.write(ts.transpileModule(fs.readFileSync(0,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)"
helpers = (subprocess.check_output(["node", "-e", transpile], input=helpers_ts.encode(), cwd=repo / "apps/web") +
           b"\nwindow.helpers={readReceipt,readLegacyAdoption,legacyIdentity,withReceiptLock};")
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','application/javascript' if self.path=='/helper.js' else 'text/html');self.end_headers();self.wfile.write(helpers if self.path=='/helper.js' else b'<script src="/helper.js"></script>')
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True);ctx=browser.new_context();a=ctx.new_page();url=f'http://127.0.0.1:{server.server_port}';a.goto(url)
  assert a.evaluate('!!navigator.locks?.request')
  a.evaluate('''() => {window.key=`yleum:amocrm:lead:v1:${location.origin}:101:max`;localStorage.setItem(key,JSON.stringify({version:1,key:'22222222-2222-4222-8222-222222222222',state:'succeeded',id:'2965379',fingerprint:'b'.repeat(64)}));localStorage.setItem(key+':legacy-adoption',JSON.stringify({version:1,key:'11111111-1111-4111-8111-111111111111',id:'2964609',requestDigest:'a'.repeat(64)}));}''')
  b=ctx.new_page();b.goto(url)
  assert b.evaluate('''() => {window.key=`yleum:amocrm:lead:v1:${location.origin}:101:max`;return sessionStorage.length===0 && helpers.readReceipt(key).id==='2965379';}''')
  assert b.evaluate('helpers.legacyIdentity(key)==="11111111-1111-4111-8111-111111111111"')
  assert b.evaluate('helpers.readReceipt(key.replace(":101:",":202:"))===null')
  assert b.evaluate('''() => {localStorage.setItem('malformed',JSON.stringify({version:1,key:'11111111-1111-4111-8111-111111111111',state:'succeeded',id:'2964609',fingerprint:'a'.repeat(64),requestDigest:{personal:'blocked'}}));try{helpers.readReceipt('malformed');return false;}catch{return true;}}''')
  a.evaluate('''() => {window.abortLock=new AbortController();window.lockEntered=false;window.hold=new Promise(resolve=>window.release=resolve);window.task=helpers.withReceiptLock('probe',abortLock.signal,async()=>{window.lockEntered=true;await hold;});}''');a.wait_for_function('window.lockEntered')
  b.evaluate('''() => {window.lockEntered=false;window.task=helpers.withReceiptLock('probe',new AbortController().signal,async()=>{window.lockEntered=true;});}''')
  b.wait_for_function("async () => (await navigator.locks.query()).pending.some(lock => lock.name==='probe')")
  assert b.evaluate('window.lockEntered') is False
  a.evaluate('abortLock.abort()');b.wait_for_function('window.lockEntered');a.evaluate('release()')
  print(json.dumps({'chromium_helpers_probes':6,'passed':6,'real_storage_and_WebLocks':True,'CRM_or_provider_requests':0,'live_MAX_acceptance':False}));browser.close()
finally:server.shutdown();server.server_close();thread.join(timeout=3)
