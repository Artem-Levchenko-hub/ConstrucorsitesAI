
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {stripTypeScriptTypes}=require('node:module');
const cache={},sent=[],trace=[];let cookie;
// Valid payload fixtures exercise route effects; framework/schema adapters are
// confined to this harness, while crypto, source imports and auth stay real.
const chain={};
for(const method of ['string','number','record','unknown','min','max','int','regex',
'uuid','optional','default','datetime','trim'])chain[method]=()=>chain;
chain.parse=value=>value;
chain.safeParse=value=>({success:true,data:value});
const z={...chain,object:()=>chain,coerce:chain};
const drizzle={};
for(const method of ['and','desc','eq','lt','or','sql'])drizzle[method]=(...args)=>args;
const env={OMNIA_PROJECT_ID:input.project,MAX_BOT_TOKEN:'test-max-token',
MAX_SESSION_SECRET:'test-session-secret',OMNIA_PUBLIC_APP_ORIGIN:input.public?'https://app.test':''};
const tableNames=['maxUsers','maxAnalyticsEvents','maxBusinessActions','maxAuditLog'];
const tables=Object.fromEntries(tableNames.map(n=>[n,{table:n,
id:n+'.id',maxUserId:n+'.maxUserId',details:n+'.details'}]));
const actionId='11111111-1111-4111-8111-111111111111';
const tx={execute:async()=>{},
insert:table=>({values:row=>({
onConflictDoNothing(){return this;},
returning:async()=>{trace.push('persisted:'+table.table);return[{...row,id:actionId}];},
then:resolve=>{trace.push('persisted:'+table.table);resolve([]);}
})}),
select:()=>({from:table=>({where:()=>({limit:async()=>[]})})})};
const db={schema:tables,withMaxUser:async(actor,fn)=>{
if(input.dbFailure)throw new Error('synthetic write failure');return fn(tx);}};
const next={NextResponse:class extends Response{
static json(data,options){const response=new this(JSON.stringify(data),options);
response.cookies={set:()=>trace.push('session-cookie')};return response;}}};
function load(path){if(cache[path])return cache[path];const result={};
const ctx={exports:result,Buffer,Date,URL,Request,Response,Headers,AbortSignal,TextEncoder,
crypto:require('node:crypto').webcrypto,
process:{env},console:{warn:()=>{},error:()=>{}},
fetch:async(url,options)=>{trace.push('forward');sent.push({url,...options});
if(input.upstreamFailure)throw new Error('synthetic private credentials');
return new Response(null,{status:204});},
require:name=>{
if(name==='@/lib/db')return db;
if(name==='next/server')return next;
if(name==='next/headers')return{
cookies:async()=>({get:()=>cookie?{value:cookie}:undefined}),
headers:async()=>new Headers()};
if(name.startsWith('@/'))return load(input.tree+'/'+name.slice(2)+'.ts');
if(name==='zod')return{z};
if(name==='drizzle-orm')return drizzle;
return require(name);
}};
let source=stripTypeScriptTypes(fs.readFileSync(path,'utf8'));
source=source.replace(/import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
(_,bindings,name)=>`const {${bindings.replace(/\bas\b/g,':')}}=require('${name}');`);
const names=[];
source=source.replace(/export\s+((?:async\s+)?(?:function|class|const|let|var)\s+(\w+))/g,
(_,declaration,name)=>{names.push(name);return declaration;});
source+='\nObject.assign(exports,{'+names.join(',')+'});';
vm.runInNewContext(source,ctx,{filename:path});cache[path]=result;return result;}
const session=load(input.tree+'/lib/max/session.ts');
if(input.actor!==null)cookie=session.createMaxSession({id:input.actor}).value;
if(input.corrupt)cookie+='tampered';
const route=load(input.tree+'/app/api/'+input.route+'/route.ts');
route.POST(new Request('https://app.test/api/'+input.route,{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(input.body)})).then(async response=>{
console.log(JSON.stringify({status:response.status,sent,trace}));
}).catch(error=>console.log(JSON.stringify({error:error.name,message:error.message,sent,trace})));
