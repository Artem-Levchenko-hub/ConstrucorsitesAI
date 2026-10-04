"""Real iframe/popup scripts keep provider configuration in the MoySklad iframe."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yleum_api.core.db import get_session
from yleum_api.core.deps import get_current_user, get_optional_user
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.routers import moysklad_vendor

CONNECT = "/api/integrations/moysklad/connect"


@pytest.fixture
def bridge(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        session_version=3,
        email="qa-owner@example.invalid",
        is_anon=False,
        email_verified_at=datetime.now(UTC),
    )
    own = SimpleNamespace(
        id=uuid4(), owner_id=user.id, name="QA_OWN_PROJECT", template="max_miniapp"
    )
    other = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), name="QA_FOREIGN_PRIVATE", template="max_miniapp"
    )
    state = SimpleNamespace(user=user, rows=[own, other], statements=[])

    class Session:
        async def execute(self, stmt):
            state.statements.append(stmt)
            return SimpleNamespace(all=lambda: state.rows)

        async def commit(self):
            pytest.fail("popup bootstrap cannot mutate")

    async def optional():
        return state.user

    async def authenticated():
        if state.user is None:
            raise ApiError("unauthorized", "QA no session", 401)
        return state.user

    async def session():
        yield Session()

    app = FastAPI()
    app.include_router(moysklad_vendor.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_optional_user] = optional
    app.dependency_overrides[get_current_user] = authenticated
    with TestClient(app, base_url="https://yleum.ru", follow_redirects=False) as client:
        yield client, state, own, other


def test_popup_lists_only_own_max_projects_without_pii_or_mutation(bridge):
    client, state, own, other = bridge
    response = client.get(CONNECT)
    assert response.status_code == 200
    assert own.name in response.text
    assert str(own.id) in response.text
    assert other.name not in response.text and str(other.id) not in response.text
    assert state.user.email not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    stmt = state.statements[0]
    assert "projects.owner_id" in str(stmt) and "projects.template" in str(stmt)
    assert stmt.compile().params["param_1"] == 100


def test_popup_login_next_is_fixed_and_no_project_data_leaks(bridge):
    client, state, own, _other = bridge
    state.user = None
    response = client.get(CONNECT + "?next=https://attacker.invalid&project=QA_OTHER")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fapi%2Fintegrations%2Fmoysklad%2Fconnect"
    assert own.name not in response.text and state.statements == []


@pytest.mark.asyncio
async def test_iframe_contains_native_project_and_warehouse_controls_without_code_copy():
    response = await moysklad_vendor.setup()
    html = bytes(response.body).decode()
    for field in ("project", "organization", "store", "connect", "save"):
        assert f'id="{field}"' in html
    assert 'id="code"' not in html
    assert "Скопируйте код" not in html
    assert CONNECT in html


NODE_HARNESS = r"""
const vm=require('node:vm'); const assert=require('node:assert/strict');
const ORIGIN='https://yleum.ru'; const PID=DATA.project;
const org='00000000-0000-4000-8000-000000000011';
const store='00000000-0000-4000-8000-000000000012';
const handlers={frame:[],popup:[]}; const sent=[]; const calls=[]; const timers=[];
class Element {
 constructor(){this.hidden=true;this.disabled=false;this.value='';this.children=[];this.textContent='';}
 appendChild(x){this.children.push(x);if(!this.value)this.value=x.value;return x;}
 replaceChildren(){this.children=[];this.value='';}
}
const elements=new Map();
const document={getElementById:id=>{if(!elements.has(id)){
 const element=new Element();if(id==='auth')element.disabled=true;elements.set(id,element);}
 return elements.get(id);},createElement:()=>new Element()};
const popupDocument={getElementById:()=>new Element()};
let correlation; let ready; let pendingClaim;let opens=0;
const frame={location:{origin:ORIGIN},parent:{postMessage:p=>{correlation=p.messageId}},
 addEventListener:(name,h)=>{if(name==='message')handlers.frame.push(h)},
 removeEventListener:(name,h)=>{handlers.frame=handlers.frame.filter(x=>x!==h)},
 open:(url)=>{opens++;assert.equal(url,'/api/integrations/moysklad/connect');
 return CASE==='blocked'?null:popup;}};
const popup={location:{origin:ORIGIN},opener:frame,closed:false,close:()=>{popup.closed=true},
 addEventListener:(name,h)=>{if(name==='message')handlers.popup.push(h)}};
const send=async(which,data,origin=ORIGIN,source=which==='frame'?popup:frame)=>{
 for(const h of [...handlers[which]])await h({origin,source,data});};
frame.postMessage=(data,target)=>{assert.equal(target,ORIGIN);sent.push(data);
 if(data.name==='MoyBridgeReady')ready=data;
 setImmediate(()=>void send('frame',data));};
popup.postMessage=(data,target)=>{assert.equal(target,ORIGIN);sent.push(data);
 setImmediate(()=>void send('popup',data));};
const popupFetch=async(url,init={})=>{
 assert.equal(init.credentials,'same-origin');calls.push({url,...init});
 if(url.endsWith('/connect/session'))return{ok:true,json:async()=>({
 session_marker:CASE==='auth-changed'?'QA_CHANGED':JSON.parse(
 DATA.popup.match(/const bridge=(.*);/)[1]).session_marker})};
 if(url.endsWith('/claim')){
  if(CASE==='unknown'||CASE==='unknown-reopen')throw Error('QA_LOST_RESPONSE');
  if(CASE==='duplicate'||CASE==='cancel-inflight')return await new Promise(resolve=>{
   pendingClaim=resolve});
  return{ok:true,json:async()=>({status:CASE==='pending'?'vendor_sync_pending':'connected'})};
 }
 if(url.endsWith('/options'))return{ok:true,json:async()=>({
 organizations:[{id:org,name:'QA_ORG'}],stores:[{id:store,name:'QA_STORE'}]})};
 if(url.endsWith('/settings'))return{ok:true,json:async()=>({status:'saved'})};
 throw Error('unapproved fetch path');
};
const deadlines=[];const contextLogs=[];
const safeConsole={...console,info:(label,value)=>{
 assert.equal(label,'yleum:moy-context:v1');contextLogs.push(JSON.parse(value));}};
const common={console:safeConsole,Math,JSON,Date,Map,Set,URL,AbortSignal,
 setTimeout:(h,ms)=>{deadlines.push({h,ms})},
 setInterval:h=>{timers.push(h);return timers.length},clearInterval:()=>{}};
let contextCalls=0;
const setupFetch=async(url,init)=>{contextCalls++;
 assert.equal(url,'/api/integrations/moysklad/context');
 assert.equal(init.credentials,'omit');
 return{ok:true,json:async()=>({code:'QA_NATIVE_CODE_MEMORY_ONLY_123',account_name:'QA_VENDOR'})};};
vm.runInNewContext(DATA.setup,{...common,window:frame,document,fetch:setupFetch});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const el=id=>document.getElementById(id);
(async()=>{
 await send('frame',{name:'UserContextResponse',
 correlationId:CASE==='context-correlation'?correlation+1:correlation,token:'QA_CONTEXT'},
 CASE==='context-origin'?'https://attacker.invalid':'https://online.moysklad.ru',
 CASE==='context-host-window'?{}:frame.parent);
 if(['context-origin','context-correlation'].includes(CASE)){
  assert.equal(contextCalls,0);assert.equal(el('auth').disabled,true);return;}
 if(CASE==='context-diagnostic'){
  assert.equal(contextLogs.length,1);
  const log=contextLogs[0];assert.deepEqual(Object.keys(log).sort(),
   ['correlation_matches','message_id_matches','name','nonce_matches','origin',
    'source_is_parent','source_is_top'].sort());
  assert.equal(log.name,'UserContextResponse');
  assert.equal(log.origin,'https://online.moysklad.ru');
  assert.equal(log.correlation_matches,true);assert.equal(log.source_is_parent,true);
  for(const key of ['correlation_matches','message_id_matches','nonce_matches',
   'source_is_parent','source_is_top'])assert.equal(typeof log[key],'boolean');
  assert.ok(!JSON.stringify(log).includes('QA_CONTEXT'));
  assert.ok(!JSON.stringify(log).includes('QA_NATIVE_CODE'));return;}
 if(CASE==='context-host-window'){
  assert.equal(contextCalls,1,'official host response must reach server token verification');
  assert.equal(el('auth').disabled,false);return;}
 assert.equal(typeof el('auth').onclick,'function');
 el('auth').onclick();await settle();
 if(CASE==='blocked'){
  assert.equal(calls.length,0);assert.match(el('status').textContent,/окн|браузер/i);return;
 }
 vm.runInNewContext(DATA.popup,{...common,window:popup,document:popupDocument,fetch:popupFetch});
 await settle();assert.ok(ready);
 assert.equal(el('project').children.length,CASE==='project-race'?2:1);
 assert.equal(el('project').value,PID);
 const request={name:'MoyBridgeRequest',nonce:ready.nonce,request_id:1,
 action:'claim',project_id:PID,code:'QA_NATIVE_CODE_MEMORY_ONLY_123'};
 if(['origin','source','nonce','foreign','malformed','action','before-save'].includes(CASE)){
  const bad={...request};let origin=ORIGIN,source=frame;
  if(CASE==='origin')origin='https://attacker.invalid';
  if(CASE==='source')source={};
  if(CASE==='nonce')bad.nonce='QA_WRONG_NONCE';
  if(CASE==='foreign')bad.project_id='00000000-0000-4000-8000-000000000099';
  if(CASE==='malformed')bad.code={token:'QA_BAD'};
  if(CASE==='action')bad.action='https://attacker.invalid';
  if(CASE==='before-save')bad.action='save';
  await send('popup',bad,origin,source);await settle();assert.equal(calls.length,0);return;
 }
 if(CASE==='expired'){
  for(const deadline of deadlines)if(deadline.ms===600000)deadline.h();
  await send('popup',request);await settle();assert.equal(calls.length,0);return;
 }
 if(CASE==='cancel-inflight'){
  el('connect').onclick();await settle();assert.ok(pendingClaim);
  await send('popup',{name:'MoyBridgeCancel',nonce:ready.nonce});
  pendingClaim({ok:true,json:async()=>({status:'connected'})});await settle();
  assert.equal(calls.filter(x=>x.url.endsWith('/options')).length,0);
  assert.match(el('status').textContent,/неизвест|проверь/i);return;
 }
 if(CASE==='cancel'){
  await send('popup',{name:'MoyBridgeCancel',nonce:ready.nonce});
  await send('popup',request);await settle();assert.equal(calls.length,0);return;
 }
 if(['frame-origin','frame-source','frame-nonce'].includes(CASE)){
  const before=el('project').children.map(x=>x.value).join(',');
  const bad={...ready,projects:[{id:'00000000-0000-4000-8000-000000000099',name:'QA_BAD'}]};
  if(CASE==='frame-nonce')bad.nonce='QA_WRONG_NONCE';
  await send('frame',bad,CASE==='frame-origin'?'https://attacker.invalid':ORIGIN,
   CASE==='frame-source'?{}:popup);
  assert.equal(el('project').children.map(x=>x.value).join(','),before);return;
 }
 if(CASE==='duplicate'){
  await send('popup',request);await send('popup',request);await settle();
  assert.equal(calls.filter(x=>x.url.endsWith('/claim')).length,1);
  pendingClaim({ok:true,json:async()=>({status:'connected'})});await settle();return;
 }
 if(CASE==='closed'){
  popup.closed=true;for(const timer of timers)timer();await settle();
  assert.equal(calls.length,0);assert.match(el('status').textContent,/окн|связ/i);return;
 }
 el('connect').onclick();
 if(CASE==='project-race'){assert.equal(el('project').disabled,true);
  el('project').value=ready.projects[1].id;}
 await settle();
 if(CASE==='auth-changed'){
  assert.equal(calls.filter(x=>x.url.endsWith('/claim')).length,0);return;
 }
 assert.equal(calls.filter(x=>x.url.endsWith('/claim')).length,1);
 const claim=calls.find(x=>x.url.endsWith('/claim'));
 assert.equal(JSON.parse(claim.body).code,'QA_NATIVE_CODE_MEMORY_ONLY_123');
 assert.equal(claim.url,`/api/projects/${PID}/app-integrations/moysklad/claim`);
 if(CASE==='unknown'||CASE==='unknown-reopen'){
  await send('popup',{...request,request_id:10});await settle();
  if(CASE==='unknown-reopen'){popup.closed=true;for(const timer of timers)timer();
   el('auth').onclick();await settle();assert.equal(opens,1);}
  assert.equal(calls.filter(x=>x.url.endsWith('/claim')).length,1);assert.match(el('status').textContent,/неизвест|проверь/i);return;
 }
 assert.equal(calls.filter(x=>x.url.endsWith('/options')).length,1);
 assert.equal(el('organization').value,org);assert.equal(el('store').value,store);
 el('save').onclick();await settle();
 const saved=calls.find(x=>x.url.endsWith('/settings'));assert.ok(saved);
 assert.deepEqual(JSON.parse(saved.body),{organization_id:org,store_id:store});
 assert.equal(saved.method,'PUT');assert.match(el('status').textContent,/сохран/i);
 assert.equal(sent.filter(x=>x.name==='MoyBridgeReady').length,1);
 assert.ok(!sent.some(x=>x.token));
 assert.ok(!JSON.stringify(sent).includes('access_token'));
})();
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "positive",
        "pending",
        "origin",
        "source",
        "nonce",
        "foreign",
        "malformed",
        "action",
        "before-save",
        "cancel",
        "duplicate",
        "unknown",
        "blocked",
        "closed",
        "frame-origin",
        "frame-source",
        "frame-nonce",
        "project-race",
        "unknown-reopen",
        "auth-changed",
        "cancel-inflight",
        "expired",
        "context-host-window",
        "context-diagnostic",
        "context-origin",
        "context-correlation",
    ],
)
async def test_actual_two_way_bridge_boundaries_and_native_controls(bridge, case):
    import asyncio
    import json
    import re
    import subprocess

    client, _state, own, _other = bridge
    if case == "project-race":
        _state.rows.append(
            SimpleNamespace(
                id=uuid4(), owner_id=_state.user.id, name="QA_SECOND_OWN", template="max_miniapp"
            )
        )
    popup = client.get(CONNECT)
    assert popup.status_code == 200
    frame = await moysklad_vendor.setup()
    scripts = []
    for html in [popup.text, bytes(frame.body).decode()]:
        match = re.search(r'<script nonce="[^"]+">(.*?)</script>', html, re.S)
        assert match is not None
        scripts.append(match.group(1))
    program = (
        "const DATA="
        + json.dumps({"popup": scripts[0], "setup": scripts[1], "project": str(own.id)})
        + ";\n"
    )
    program += "const CASE=" + json.dumps(case) + ";\n" + NODE_HARNESS
    run = await asyncio.to_thread(
        subprocess.run, ["node", "-e", program], capture_output=True, text=True, timeout=10
    )
    assert run.returncode == 0, run.stderr


def test_popup_session_marker_is_readonly_and_changes_with_owner_version(bridge):
    client, state, _own, _other = bridge
    first = client.get(CONNECT + "/session")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    marker = first.json()["session_marker"]
    assert len(marker) == 64 and str(state.user.id) not in first.text
    assert state.user.email not in first.text and state.statements == []
    state.user.session_version += 1
    assert client.get(CONNECT + "/session").json()["session_marker"] != marker
    state.user = None
    assert client.get(CONNECT + "/session").status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "foreign_project",
        "foreign_account_owner",
        "expired",
        "suspended",
        "uninstalled",
        "prior_manual",
        "prior_other_account",
    ],
)
async def test_existing_claim_guards_reject_before_any_writes(bridge, monkeypatch, fault):
    from datetime import timedelta

    from yleum_api.models.project import Project

    _client, state, project, _other = bridge
    installation = SimpleNamespace(
        pairing_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        state="installed",
        token_enc="QA_ENCRYPTED_ONLY",
        user_id=None,
        account_id=uuid4(),
    )
    previous = None
    if fault == "foreign_project":
        project.owner_id = uuid4()
    elif fault == "foreign_account_owner":
        installation.user_id = uuid4()
    elif fault == "expired":
        installation.pairing_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif fault in {"suspended", "uninstalled"}:
        installation.state = fault
    elif fault == "prior_manual":
        previous = SimpleNamespace(auth_mode="credentials", public_config={})
    elif fault == "prior_other_account":
        previous = SimpleNamespace(
            auth_mode="connector", public_config={"moysklad_account_id": str(uuid4())}
        )

    class GuardSession:
        def __init__(self):
            self.reads = 0

        async def get(self, model, key):
            assert model is Project and key == project.id
            return project

        async def execute(self, stmt):
            self.reads += 1
            value = installation if self.reads == 1 else previous
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        def add(self, *_args):
            pytest.fail("rejected claim attempted a write")

        async def flush(self):
            pytest.fail("rejected claim attempted a flush")

        async def commit(self):
            pytest.fail("rejected claim attempted a commit")

    async def entitled(*_args):
        return None

    monkeypatch.setattr(moysklad_vendor, "assert_integrations_allowed", entitled)
    with pytest.raises(ApiError) as caught:
        await moysklad_vendor.claim(
            project.id,
            moysklad_vendor.ClaimRequest(code="QA_MEMORY_ONLY_CODE_123456"),
            GuardSession(),
            state.user,
        )
    assert caught.value.code in {
        "not_found",
        "moysklad_already_linked",
        "moysklad_pairing_invalid",
        "moysklad_connection_conflict",
    }


@pytest.mark.asyncio
async def test_vendor_context_non_admin_cannot_issue_pairing_code(monkeypatch):
    import httpx

    original = httpx.AsyncClient
    monkeypatch.setattr(moysklad_vendor, "_outbound_jwt", lambda: "QA_NOT_REAL_VENDOR_JWT")
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"accountId": str(uuid4()), "role": "user"})
    )
    monkeypatch.setattr(
        moysklad_vendor.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    with pytest.raises(ApiError) as caught:
        await moysklad_vendor.pairing_code(
            moysklad_vendor.ContextRequest(token="QA_VENDOR_CONTEXT_TOKEN"), None
        )
    assert caught.value.code == "moysklad_admin_required"
