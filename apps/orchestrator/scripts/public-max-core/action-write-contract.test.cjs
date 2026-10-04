/* RED contract tests execute shipped routes, with an in-memory DB boundary.
 * DB locking/RLS/races require the separate disposable PostgreSQL acceptance.
 * NODE_PATH is unnecessary; QA dependencies can be supplied with OMNIA_TEST_NODE_ROOT.
 */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const {createRequire}=require('node:module');
const vm=require('node:vm');
const {randomUUID}=require('node:crypto');
const root=process.env.OMNIA_ACTION_TEST_TEMPLATE_ROOT || resolve(__dirname,'../../templates/max-miniapp-nextjs');
const qaRequire=createRequire(resolve(process.env.OMNIA_TEST_NODE_ROOT || root,'package.json'));
const ts=qaRequire('typescript');
let zod; try {zod=qaRequire('zod');} catch {zod=qaRequire('next/dist/compiled/zod');}
function fixture() {
  const rows=[], audits=[];
  const tables={maxUsers:{maxUserId:'maxUserId'},maxBusinessActions:{id:'id',maxUserId:'maxUserId'},maxAuditLog:{id:'id',details:'details',maxUserId:'maxUserId'}};
  function match(row,c){if(!c)return true;if(c.kind==='and')return c.values.every(x=>match(row,x));if(c.kind==='json')return row.details[c.field]===c.value;return row[c.column]===c.value;}
  const tx={
    execute:async()=>{},
    select(){let table,condition,count;const q={from(t){table=t;return this;},where(c){condition=c;return this;},limit(n){count=n;return this;},for(){return this;},then(resolve,reject){return Promise.resolve((table===tables.maxBusinessActions?rows:audits).filter(r=>match(r,condition)).slice(0,count)).then(resolve,reject);}};return q;},
    insert(table){let value,inserted;const q={values(v){value=v;return this;},onConflictDoNothing(){return this;},async returning(){if(table===tables.maxUsers)return[];if(!inserted){inserted={id:randomUUID(),status:'new',createdAt:new Date(),updatedAt:new Date(),...value};(table===tables.maxBusinessActions?rows:audits).push(inserted);}return[{...inserted}];},then(resolve,reject){return this.returning().then(resolve,reject);}};return q;},
    delete(table){let condition;return{where(c){condition=c;return this;},async returning(){const source=table===tables.maxBusinessActions?rows:audits;const removed=source.filter(r=>match(r,condition));for(const row of removed)source.splice(source.indexOf(row),1);return removed;},then(resolve,reject){return this.returning().then(resolve,reject);}};},
    update(table){assert.equal(table,tables.maxBusinessActions);let value,condition;return{set(v){value=v;return this;},where(c){condition=c;return this;},async returning(){const row=rows.find(r=>match(r,condition));if(!row)return[];Object.assign(row,value);return[{...row}];}};},
  };
  const modules={
    'node:crypto':require('node:crypto'), 'zod':zod,
    'drizzle-orm':{eq:(column,value)=>({kind:'eq',column,value}),and:(...values)=>({kind:'and',values}),sql:(strings,...values)=>({kind:'json',field:strings.join('').includes('operationKey')?'operationKey':'actionId',value:values[1]})},
    'next/server':{NextResponse:{json:(data,init)=>Response.json(data,init)}},
    '@/lib/db':{schema:tables,withMaxUser:async(actor,run)=>{assert.equal(actor,'test-actor');return run(tx);}},
    '@/lib/max/session':{getMaxUser:async()=>({id:'test-actor'})},
  };
  const load=(relative)=>{const context={exports:{},require:name=>{assert.ok(name in modules,`Unexpected import ${name}`);return modules[name];},TextEncoder,Date};vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root,relative),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);return context.exports;};
  const item=load('src/app/api/omnia/actions/[id]/route.ts');
  return {rows,audits,post:load('src/app/api/omnia/actions/route.ts').POST,patch:item.PATCH,get:item.GET,remove:item.DELETE};
}
function request(body,headers={}){return new Request('https://test.invalid/api/omnia/actions',{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});}
const input={actionType:'booking',payload:{slot:'2026-10-20T10:00:00Z'}};
test('create rejects a missing operation identity before any durable insert',async()=>{const f=fixture();const response=await f.post(request(input));assert.equal(response.status,428);assert.equal(f.rows.length,0);});
test('same operation identity replays the original server ID after a lost response',async()=>{const f=fixture(),operationKey=randomUUID();const first=await f.post(request({...input,operationKey}));const original=await first.json();const replay=await f.post(request({...input,operationKey}));assert.equal(replay.status,201);assert.equal((await replay.json()).action.id,original.action.id);assert.equal(f.rows.length,1);});
test('reusing an operation identity for changed payload returns conflict',async()=>{const f=fixture(),operationKey=randomUUID();await f.post(request({...input,operationKey}));const response=await f.post(request({...input,payload:{slot:'2026-10-20T11:00:00Z'},operationKey}));assert.equal(response.status,409);assert.equal(f.rows.length,1);});
test('legacy full-payload PATCH without expected baseline cannot overwrite a saved record',async()=>{const f=fixture();const saved=await(await f.post(request({...input,operationKey:randomUUID()}))).json();const response=await f.patch(request({payload:{slot:'changed'}}),{params:Promise.resolve({id:saved.action.id})});assert.equal(response.status,428);assert.equal(f.rows[0].payload.slot,input.payload.slot);});

function clientFixture(fetch, saved=new Map(), storageOverride, sessionActor=()=>'test-actor') {
 const window={location:{origin:'https://test.invalid'},sessionStorage:storageOverride || {getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)}};
 const wrappedFetch=(url,init)=>url==='/api/max/session'?Promise.resolve(Response.json({user:{id:sessionActor()}})):fetch(url,init);
 const context={exports:{},require:name=>{assert.equal(name,'@/lib/max/bridge');return{getMaxWebApp:()=>null};},fetch:wrappedFetch,window,crypto:require('node:crypto').webcrypto,TextEncoder,URLSearchParams};
 vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root,'src/lib/omnia/integration-client.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
 return context.exports;
}
test('SDK normal retry after unknown create outcome retains operation identity; confirmed next intent renews it',async()=>{
 const sent=[];
 const sdk=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));if(sent.length===1)throw new TypeError('synthetic response loss after commit');return Response.json({action:{id:randomUUID()}},{status:201});});
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload),TypeError);
 await sdk.createMaxAction(input.actionType,input.payload);
 await sdk.createMaxAction(input.actionType,input.payload);
 assert.equal(typeof sent[0].operationKey,'string');
 assert.equal(sent[1].operationKey,sent[0].operationKey);
 assert.notEqual(sent[2].operationKey,sent[0].operationKey);
});
test('SDK independent concurrent identical submissions carry distinct explicit identities',async()=>{
 const sent=[];
 const sdk=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));return Response.json({action:{id:randomUUID()}},{status:201});});
 await Promise.all([sdk.createMaxAction(input.actionType,input.payload),sdk.createMaxAction(input.actionType,input.payload)]);
 assert.equal(sent.length,2);
 assert.equal(typeof sent[0].operationKey,'string');
 assert.notEqual(sent[0].operationKey,sent[1].operationKey);
});

test('stale full-payload PATCH receives412 and latest baseline progresses even within one tick',async()=>{
 const f=fixture();const saved=await(await f.post(request({...input,operationKey:randomUUID()}))).json();
 const context={params:Promise.resolve({id:saved.action.id})};
 const baseline=await f.get(request({}),context);const revision=baseline.headers.get('ETag');assert.equal(revision,saved.action.revision);
 const first=await f.patch(request({payload:{slot:'first',editorA:true}},{'If-Match':revision}),context);assert.equal(first.status,200);
 const stale=await f.patch(request({payload:{slot:'second',editorB:true}},{'If-Match':revision}),context);assert.equal(stale.status,412);assert.equal(f.rows[0].payload.editorA,true);assert.equal(f.rows[0].payload.editorB,undefined);
 const next=first.headers.get('ETag');assert.notEqual(next,revision);
 const latest=await f.patch(request({status:'done'},{'If-Match':next}),context);assert.equal(latest.status,200);assert.notEqual(latest.headers.get('ETag'),next);
});
test('SDK explicit operation key binds retries while explicit newIntent separates an uncertain identical submission',async()=>{
 const sent=[];const sdk=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));if(sent.length===1)throw Error('unknown');return Response.json({action:{id:randomUUID()}},{status:201});});
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));
 await sdk.createMaxAction(input.actionType,input.payload,{newIntent:true});assert.notEqual(sent[0].operationKey,sent[1].operationKey);
 const operationKey=randomUUID();await sdk.createMaxAction(input.actionType,input.payload,{operationKey});await sdk.createMaxAction(input.actionType,input.payload,{operationKey});assert.equal(sent[2].operationKey,operationKey);assert.equal(sent[3].operationKey,operationKey);
});

test('SDK reload retries the durable uncertain identity without storing business payload',async()=>{
 const sent=[],storage=new Map();
 const before=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));throw Error('lost committed response');},storage);
 await assert.rejects(before.createMaxAction(input.actionType,input.payload));
 assert.ok(!JSON.stringify([...storage.values()]).includes(input.payload.slot));
 const after=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));return Response.json({action:{id:randomUUID()}},{status:201});},storage);
 await after.createMaxAction(input.actionType,input.payload);assert.equal(sent[1].operationKey,sent[0].operationKey);assert.equal(storage.size,0);
});
test('SDK storage failure prevents sending an unrepeatable action',async()=>{
 let writes=0;const sdk=clientFixture(async()=>{writes++;return Response.json({});},new Map(),{getItem:()=>null,setItem:()=>{throw Error('storage unavailable');},removeItem:()=>{}});
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));assert.equal(writes,0);
});
test('SDK update preserves caller form baseline without rereading immediately before save',async()=>{
 const sent=[];const sdk=clientFixture(async(url,init)=>{sent.push({url,init});return Response.json({action:{id:randomUUID()}},{status:200});});
 const revision='"'+ 'a'.repeat(64)+'"';await sdk.updateMaxAction(randomUUID(),{payload:{note:'edit'}},revision);
 assert.equal(sent.length,1);assert.equal(sent[0].init.method,'PATCH');assert.equal(sent[0].init.headers['If-Match'],revision);
});


test('SDK equivalent actual JSON wire payload preserves uncertain retry identity including Dates and omitted fields',async()=>{
 const sent=[];const sdk=clientFixture(async(_url,init)=>{sent.push(JSON.parse(init.body));if(sent.length===1)throw Error('lost committed response');return Response.json({action:{id:randomUUID()}},{status:201});});
 await assert.rejects(sdk.createMaxAction('booking',{slot:new Date('2026-10-20T10:00:00Z'),omitted:undefined}));
 await sdk.createMaxAction('booking',{slot:'2026-10-20T10:00:00.000Z'});
 assert.deepEqual(sent[0].payload,sent[1].payload);assert.equal(sent[0].operationKey,sent[1].operationKey);
});
test('changing a normal action to health type cannot erase its immutable operation receipt',async()=>{
 const f=fixture(),operationKey=randomUUID();const saved=await(await f.post(request({...input,operationKey}))).json();const context={params:Promise.resolve({id:saved.action.id})};
 assert.equal((await f.patch(request({actionType:'omnia_health_fake'},{'If-Match':saved.action.revision}),context)).status,200);
 assert.equal((await f.remove(request({}),context)).status,200);
 assert.equal((await f.post(request({...input,operationKey}))).status,410);assert.equal(f.rows.length,0);assert.equal(f.audits.length,1);
});

test('SDK binds captured session actor to write; account switch rejects before commit and same actor uncertain retry stays stable',async()=>{
 let sessionActor='actor-A',committed=0;const sent=[];
 const sdk=clientFixture(async(_url,init)=>{
  sent.push({body:JSON.parse(init.body),actor:init.headers['X-Omnia-Actor']});
  if(init.headers['X-Omnia-Actor']!=='actor-B')return Response.json({error:'Actor changed',code:'action_actor_changed'},{status:409});
  committed++;if(committed===1)throw Error('lost response');return Response.json({action:{id:randomUUID()}},{status:201});
 },new Map(),undefined,()=>sessionActor);
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));assert.equal(committed,0);assert.equal(sent[0].actor,'actor-A');
 sessionActor='actor-B';await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));await sdk.createMaxAction(input.actionType,input.payload);
 assert.equal(sent[1].actor,'actor-B');assert.equal(sent[2].body.operationKey,sent[1].body.operationKey);
});
test('POST refuses changed expected actor before any business or receipt insert',async()=>{
 const f=fixture();const response=await f.post(request({...input,operationKey:randomUUID()},{'X-Omnia-Actor':'different-actor'}));
 assert.equal(response.status,409);assert.equal(f.rows.length,0);assert.equal(f.audits.length,0);
});

test('an actor mismatch after an earlier unknown commit preserves the original actor pending identity',async()=>{
 const sent=[];let attempt=0;const sdk=clientFixture(async(_url,init)=>{
  sent.push(JSON.parse(init.body));attempt++;
  if(attempt===1)throw Error('A committed but response lost');
  if(attempt===2)return Response.json({error:'Actor changed',code:'action_actor_changed'},{status:409});
  return Response.json({action:{id:randomUUID()}},{status:201});
 });
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));await sdk.createMaxAction(input.actionType,input.payload);
 assert.equal(sent[1].operationKey,sent[0].operationKey);assert.equal(sent[2].operationKey,sent[0].operationKey);
});
test('a later authorization denial cannot clear an earlier unknown commit identity',async()=>{
 const sent=[];const sdk=clientFixture(async(_url,init)=>{
  sent.push(JSON.parse(init.body));if(sent.length===1)throw Error('committed but response lost');
  if(sent.length===2)return Response.json({error:'Access denied'},{status:403});
  return Response.json({action:{id:randomUUID()}},{status:201});
 });
 await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));await assert.rejects(sdk.createMaxAction(input.actionType,input.payload));await sdk.createMaxAction(input.actionType,input.payload);
 assert.equal(sent[2].operationKey,sent[0].operationKey);
});

test('legacy client module create uses the same retry-safe managed helper and actor precondition',async()=>{
 const sent=[];const fetch=async(_url,init)=>{sent.push(JSON.parse(init.body));if(sent.length===1)throw Error('lost committed response');return Response.json({action:{id:randomUUID()}},{status:201});};
 const canonical=clientFixture(fetch);const context={exports:{},fetch,require:name=>{if(name==='@/lib/omnia/integration-client')return canonical;assert.equal(name,'@/lib/max/bridge');return{getMaxWebApp:()=>null};},URLSearchParams};
 vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root,'src/lib/omnia/client.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
 const legacy=context.exports;await assert.rejects(legacy.createMaxAction(input.actionType,input.payload));await legacy.createMaxAction(input.actionType,input.payload);
 assert.equal(typeof sent[0].operationKey,'string');assert.equal(sent[1].operationKey,sent[0].operationKey);assert.equal(legacy.createMaxAction,canonical.createMaxAction);
});

// Execute the shipped browser SDK and renewal wrapper together. The HTTP/editor
// boundary is in memory; no database, real provider or signed cookie is used.
function previewActionFixture({sessionStatus=401,changedActor=false,loseFirstResponse=false,writeStatus=201}={}) {
 const sent=[], listeners=new Set(), saved=new Map();let expired=true, renewals=0;
 const origin='https://test.invalid';
 const preview=()=>Response.json({mode:'preview',user:{id:'preview'}});
 const native=async(input,init)=>{
  const url=String(input);
  if(url==='/api/max/session') {
   if(changedActor)return Response.json({mode:'max',user:{id:'123'}});
   return expired?Response.json({error:'Session unavailable'},{status:sessionStatus}):preview();
  }
  if(url.includes('/api/omnia/preview-session?')){renewals++;expired=false;return preview();}
  assert.equal(url,'/api/omnia/actions');sent.push({body:JSON.parse(init.body),actor:init.headers['X-Omnia-Actor']});
  if(loseFirstResponse&&sent.length===1){expired=true;throw new TypeError('synthetic response loss after commit');}
  return writeStatus===401?Response.json({error:'Denied'},{status:401}):Response.json({action:{id:'saved-action'}},{status:201});
 };
 const parent={postMessage(message){queueMicrotask(()=>listeners.forEach(fn=>fn({source:parent,origin:'https://yleum.ru',data:{type:'omnia:preview-session:result',nonce:message.nonce,url:origin+'/api/omnia/preview-session?signature=synthetic'}})));}};
 const window={fetch:native,location:{origin,href:origin+'/'},parent,crypto:require('node:crypto').webcrypto,
  sessionStorage:{getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)},
  setTimeout,clearTimeout,addEventListener:(_type,fn)=>listeners.add(fn),removeEventListener:(_type,fn)=>listeners.delete(fn)};
 function load(relative,modules={}) {
  const context={exports:{},require:name=>{assert.ok(name in modules,`Unexpected import ${name}`);return modules[name];},
   window,fetch:(...args)=>window.fetch(...args),crypto:window.crypto,TextEncoder,URLSearchParams,URL,Request,Response,AbortController};
  vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root,relative),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
  return context.exports;
 }
 const stop=load('src/lib/max/owner-preview-renewal.ts').installOwnerPreviewFetch(window);
 const sdk=load('src/lib/omnia/integration-client.ts',{'@/lib/max/bridge':{getMaxWebApp:()=>null}});
 return {sdk,stop,sent,saved,renewals:()=>renewals};
}
test('SDK create renews expired owner preview before actor preflight and dispatches one unchanged action',async()=>{
 const f=previewActionFixture();const operationKey=randomUUID();
 try {
  const result=await f.sdk.createMaxAction(input.actionType,input.payload,{operationKey});
  assert.equal(result.action.id,'saved-action');assert.equal(f.renewals(),1);assert.equal(f.sent.length,1);
  assert.equal(f.sent[0].actor,'preview');assert.equal(f.sent[0].body.operationKey,operationKey);assert.deepEqual(f.sent[0].body.payload,input.payload);
 } finally {f.stop();}
});
test('SDK lost-response retry through renewed preview keeps its operation identity without automatic write replay',async()=>{
 const f=previewActionFixture({loseFirstResponse:true});
 try {
  await assert.rejects(f.sdk.createMaxAction(input.actionType,input.payload));assert.equal(f.sent.length,1);assert.equal(f.saved.size,1);
  await f.sdk.createMaxAction(input.actionType,input.payload);assert.equal(f.renewals(),2);assert.equal(f.sent.length,2);
  assert.equal(f.sent[1].body.operationKey,f.sent[0].body.operationKey);assert.equal(f.saved.size,0);
 } finally {f.stop();}
});
for(const options of [{sessionStatus:403},{changedActor:true}])test('SDK renewal refuses '+JSON.stringify(options)+' before any business write',async()=>{
 const f=previewActionFixture(options);
 try {await assert.rejects(f.sdk.createMaxAction(input.actionType,input.payload));assert.equal(f.renewals(),0);assert.equal(f.sent.length,0);assert.equal(f.saved.size,0);} finally {f.stop();}
});
test('SDK dispatched action401 is returned without replay or clearing its uncertain identity',async()=>{
 const f=previewActionFixture({writeStatus:401});
 try {await assert.rejects(f.sdk.createMaxAction(input.actionType,input.payload));assert.equal(f.renewals(),1);assert.equal(f.sent.length,1);assert.equal(f.saved.size,1);} finally {f.stop();}
});
