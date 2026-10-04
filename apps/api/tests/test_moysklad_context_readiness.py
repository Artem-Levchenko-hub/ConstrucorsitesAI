"""Execute the actual setup script against bounded host readiness scenarios."""

import asyncio
import json
import re
import subprocess

import pytest

from yleum_api.routers import moysklad_vendor

HARNESS = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),handlers=[],deadlines=[],requests=[],posts=[],logs=[];
const doc={getElementById:id=>{if(!elements.has(id))elements.set(id,{
 disabled:id==='auth'||id==='retry',hidden:id==='retry',textContent:''});
 return elements.get(id);}};
const host={postMessage:(data,target)=>{
 assert.equal(target,'https://online.moysklad.ru');requests.push(data);}};
const frame={location:{origin:'https://yleum.ru'},parent:host,
 addEventListener:(name,h)=>{if(name==='message')handlers.push(h);},
 removeEventListener:(name,h)=>{const i=handlers.indexOf(h);if(i>=0)handlers.splice(i,1);},
 open:()=>{throw Error('no login or claim authorized by this fixture');}};
const fetch=async(url,init)=>{posts.push({url,init});return{
 ok:CASE!=='exchange-failure',json:async()=>({code:'QA_CONTEXT_CODE_MEMORY_ONLY_123'})};};
vm.runInNewContext(SCRIPT,{window:frame,document:doc,Math,JSON,Set,Map,fetch,
 console:{info:(label,value)=>logs.push({label,value})},
 setTimeout:(h,ms)=>{deadlines.push({h,ms});}});
const el=id=>doc.getElementById(id);
const send=async(data,origin='https://online.moysklad.ru')=>{
 for(const h of [...handlers])await h({data,origin,source:{officialOtherWindow:true}});};
const reply=id=>({name:'UserContextResponse',correlationId:id,token:'QA_TOKEN_MEMORY_ONLY'});
const timeout=()=>{const d=deadlines.find(x=>x.ms===12000);assert.ok(d);d.h();};
(async()=>{
 assert.equal(requests.length,1);const first=requests[0].messageId;
 assert.ok(Number.isInteger(first));assert.equal(requests[0].name,'UserContextRequest');
 if(CASE==='late'){
  timeout();await send(reply(first));assert.equal(posts.length,0);
  assert.equal(el('auth').disabled,true);return;
 }
 if(CASE==='rejection'){
  await send({name:'InvalidMessageError',correlationId:first,errors:['PRIVATE_HOST_BODY']});
  assert.match(el('status').textContent,/протокол|отклонил/i);
  timeout();assert.match(el('status').textContent,/протокол|отклонил/i);
  assert.equal(posts.length,0);assert.ok(!el('status').textContent.includes('PRIVATE'));return;
 }
 if(CASE==='retry'){
  timeout();assert.equal(el('retry').hidden,false);assert.equal(el('retry').disabled,false);
  el('retry').onclick();const second=requests[1].messageId;
  assert.notEqual(first,second);assert.equal(requests.length,2);
  await send(reply(first));await send(reply(second),'https://attacker.invalid');
  assert.equal(posts.length,0);assert.equal(el('auth').disabled,true);
  await send(reply(second));await send(reply(second));
  assert.equal(posts.length,1);assert.equal(el('auth').disabled,false);
  assert.equal(posts[0].url,'/api/integrations/moysklad/context');
  assert.equal(posts[0].init.credentials,'omit');assert.equal(posts[0].init.redirect,'error');
  assert.deepEqual(JSON.parse(posts[0].init.body),{token:'QA_TOKEN_MEMORY_ONLY'});
  el('retry').onclick();assert.equal(requests.length,2);
  assert.ok(!JSON.stringify(logs).includes('QA_TOKEN_MEMORY_ONLY'));return;
 }
 if(CASE==='bounded'){
  for(let n=0;n<3;n++){
   deadlines.filter(x=>x.ms===12000).at(-1).h();el('retry').onclick();
  }
  assert.equal(requests.length,3);assert.equal(el('retry').disabled,true);
  assert.equal(new Set(requests.map(x=>x.messageId)).size,3);return;
 }
 if(CASE==='exchange-failure'){
  await send(reply(first));assert.equal(posts.length,1);assert.equal(el('auth').disabled,true);
  timeout();assert.match(el('status').textContent,/подтвердить/i);
  assert.equal(el('retry').disabled,true);return;
 }
 throw Error('unknown fixture');
})();
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["late", "retry", "rejection", "bounded", "exchange-failure"])
async def test_actual_context_request_lifecycle(case):
    response = await moysklad_vendor.setup()
    script = re.search(r'<script nonce="[^"]+">(.*?)</script>', response.body.decode(), re.S).group(
        1
    )
    program = "const SCRIPT=" + json.dumps(script) + ";const CASE=" + json.dumps(case) + ";"
    result = await asyncio.to_thread(
        subprocess.run,
        ["node", "-e", program + HARNESS],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
