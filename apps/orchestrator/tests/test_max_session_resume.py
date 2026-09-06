"""Run canonical MAX session route and signature code using Node's TS erasure."""

import json
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "templates/max-miniapp-nextjs/src"
_SCRIPT = r"""
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const {stripTypeScriptTypes}=require('node:module');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let cookie, writes=0, cookieSets=0; const cache={};
class NextResponse extends Response {
 constructor(body,options){super(body,options);this.cookies={set:(name,value)=>{cookie=value;cookieSets++;}};}
 static json(body,options){return new NextResponse(JSON.stringify(body),options);}
}
const db={query:{maxUsers:{findFirst:async()=>null}},
 insert:()=>({values:async()=>{writes++;}})};
function load(file){
 if(cache[file])return cache[file];
 const ctx={exports:{},Buffer,URLSearchParams,Date,console:{warn(){},error(){}},
 process:{env:{MAX_BOT_TOKEN:'synthetic-bot',MAX_SESSION_SECRET:'synthetic-session'}},
 require:name=>{
  if(name==='node:crypto')return crypto;
  if(name==='next/server')return {NextResponse};
  if(name==='drizzle-orm')return {eq:()=>true};
  if(name==='@/lib/db')return {db,schema:{maxUsers:{maxUserId:'id'}}};
  if(name==='next/headers')return {
   cookies:async()=>{if(input.infrastructure)throw Error('secret-internal-error');
                    return {get:()=>cookie?{value:cookie}:undefined};},
   headers:async()=>new Headers(input.headers||{}),
  };
  if(name.startsWith('@/'))return load(input.root+'/'+name.slice(2)+'.ts');
  throw Error('unexpected import');
 }};
 let source=stripTypeScriptTypes(fs.readFileSync(file,'utf8'));const names=[];
 source=source.replace(/import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
  (_,bindings,name)=>`const {${bindings.replace(/\bas\b/g,':')}}=require('${name}');`);
 source=source.replace(/export\s+((?:async\s+)?(?:function|class|const|let|var)\s+(\w+))/g,
  (_,declaration,name)=>{names.push(name);return declaration;});
 vm.runInNewContext(source+'\nObject.assign(exports,{'+names.join(',')+'});',ctx);
 return cache[file]=ctx.exports;
}
const session=load(input.root+'/lib/max/session.ts');
if(input.user!==null)cookie=session.createMaxSession({id:input.user,firstName:'QA',lastName:null,
 username:null,languageCode:null,photoUrl:null},{maxAge:input.expired?-1:900}).value;
if(input.corrupt)cookie+='bad';
const route=load(input.root+'/app/api/max/session/route.ts');
(async()=>{
 let launched;
 if(input.launch){
  const values={auth_date:String(Math.floor(Date.now()/1000)),
                user:JSON.stringify({id:42,first_name:'QA'})};
  const key=crypto.createHmac('sha256','WebAppData').update('synthetic-bot').digest();
  values.hash=crypto.createHmac('sha256',key).update(Object.keys(values).sort()
     .map(k=>k+'='+values[k]).join('\n')).digest('hex');
  const initData=Object.entries(values).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
  const response=await route.POST(new Request('https://qa.test/api/max/session',{
   method:'POST',body:JSON.stringify({initData})}));
  launched={status:response.status,body:await response.json(),cookieSet:!!cookie};
 }
 const response=route.GET?await route.GET():new NextResponse('{}',{status:405});
 console.log(JSON.stringify({status:response.status,body:await response.json(),
                            cache:response.headers.get('cache-control'),writes,cookieSets,launched}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""


def session_request(**options):
    result = subprocess.run(
        ["node", "-e", _SCRIPT],
        input=json.dumps({"root": str(_ROOT), "user": "42", **options}),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_signed_cookie_session_resumes_without_database_write():
    result = session_request()
    assert result["status"] == 200
    assert result["body"]["user"]["id"] == "42"
    assert result["cache"] == "no-store"
    assert result["writes"] == 0
    assert result["cookieSets"] == 0


@pytest.mark.parametrize(
    "options",
    [
        {"user": None},
        {"corrupt": True},
        {"expired": True},
        {"user": "preview"},
        {"user": "0"},
        {"user": "-42"},
        {"user": "1.5"},
        {"user": None, "headers": {"x-omnia-max-user-id": "42"}},
    ],
)
def test_session_resume_rejects_unsigned_invalid_or_preview_identity(options):
    result = session_request(**options)
    assert result["status"] == 401
    assert "user" not in result["body"]
    assert result["cache"] == "no-store"
    assert result["writes"] == 0
    assert result["cookieSets"] == 0


def test_session_infrastructure_failure_is_safe_and_not_cacheable():
    result = session_request(infrastructure=True)
    assert result["status"] == 503
    assert "secret-internal-error" not in json.dumps(result)
    assert result["cache"] == "no-store"
    assert result["writes"] == 0
    assert result["cookieSets"] == 0


def test_existing_post_launch_persists_user_cookie_and_can_resume():
    result = session_request(user=None, launch=True)
    assert result["launched"]["status"] == 200
    assert result["launched"]["cookieSet"] is True
    assert result["launched"]["body"]["user"]["id"] == "42"
    assert result["writes"] == 1
    assert result["cookieSets"] == 1
    assert result["status"] == 200
    assert result["body"]["user"]["id"] == "42"
