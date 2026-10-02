import * as fs from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

export function redact(value, secrets=[]) {
  let text = String(value ?? '');
  for (const secret of secrets.filter(Boolean).sort((a,b)=>b.length-a.length)) text=text.split(secret).join('[SECRET]');
  return text.replace(/https?:\/\/[^\s<>"']+/g, url=>url.split(/[?#]/)[0])
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,'[EMAIL]')
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi,'[ID]')
    .replace(/\b(?:Bearer\s+)?[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\b/g,'[TOKEN]')
    .replace(/\bBearer\s+\S+/gi,'Bearer [TOKEN]')
    .replace(/\b[0-9a-f]{80,}\b/gi,'[AUTH_CODE]')
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g,'[IP]')
    .replace(/cell-[a-z0-9-]+\.[a-z0-9.-]+/gi,'[PREVIEW_HOST]')
    .replace(/((?:token|signature|password|secret|authorization|cookie)\s*[=:]\s*)[^\s,;]+/gi,'$1[SECRET]');
}

export async function createRecorder({publicDir,privateDir,secrets=[],catalog=[]}) {
  await fs.mkdir(publicDir,{recursive:true});
  await fs.mkdir(privateDir,{recursive:true});
  const statusPath=path.join(publicDir,'status.json');
  let state;
  try {state=JSON.parse(await fs.readFile(statusPath,'utf8'));}
  catch(e) {if(e.code!=='ENOENT') throw e; state={scenarios:{},totalScenarios:catalog.length};}
  for(const criterion of catalog) state.scenarios[criterion.id]??={status:'NOT_RUN',expected:criterion.expected??'',events:0};
  const session=randomUUID(); let sequence=0,current=null,queue=Promise.resolve();
  const safe = text=>redact(text,secrets);
  async function saveStatus() {
    state.updatedAt=new Date().toISOString();
    const temp=statusPath+'.'+session+'.tmp';
    await fs.writeFile(temp,JSON.stringify(state,null,2),'utf8');
    await fs.rename(temp,statusPath);
  }
  await saveStatus();
  async function append(event) {await fs.appendFile(path.join(publicDir,'events.jsonl'),JSON.stringify(event)+'\n','utf8');}
  async function writeRecord(action,observation={}) {
    const id=session+'-'+String(++sequence).padStart(5,'0');
    const rawFile=id+'.json'; const screenshotFile=observation.screenshot?id+'.png':null;
    const {screenshot,...raw}=observation;
    await fs.writeFile(path.join(privateDir,rawFile),JSON.stringify({scenario:current,action,...raw},null,2),'utf8');
    if(screenshot) await fs.writeFile(path.join(privateDir,screenshotFile),screenshot);
    const event={id,phase:'after',time:new Date().toISOString(),scenario:current,action,actionSucceeded:!observation.error,
      durationMs:observation.durationMs??null,url:safe(observation.url),title:safe(observation.title),
      dom:safe(observation.dom),logs:(observation.logs??[]).map(log=>({...log,message:safe(log.message),url:safe(log.url)})),
      error:observation.error?safe(observation.error):null,captureErrors:(observation.captureErrors??[]).map(safe),
      captureComplete:!(observation.captureErrors?.length),rawFile,screenshotFile,
      screenshotSha256:screenshot?createHash('sha256').update(screenshot).digest('hex'):null};
    await append(event);
    if(current) {state.scenarios[current].events++; state.scenarios[current].lastEvent=id;}
    await saveStatus();
    return event;
  }
  function record(action,observation) {
    const result=queue.then(()=>writeRecord(action,observation));
    queue=result.catch(()=>{}); return result;
  }
  async function capture(tab) {
    const observation={captureErrors:[]};
    for(const [key,read] of [
      ['url',()=>tab.url()],['title',()=>tab.title()],['dom',()=>tab.playwright.domSnapshot()],
      ['logs',()=>tab.dev.logs({levels:['warn','error'],limit:60})],['screenshot',()=>tab.screenshot({fullPage:false})]
    ]) {try {observation[key]=await read();} catch(e) {observation.captureErrors.push(key+': '+e.message);}}
    return observation;
  }
  async function run(action,fn,tab) {
    if(!current) throw Error('Select a scenario before performing an action');
    await append({phase:'before',time:new Date().toISOString(),scenario:current,action});
    await saveStatus();
    const started=Date.now(); let value,error;
    try {value=await fn();} catch(e) {error=e;}
    const observation=await capture(tab);
    observation.durationMs=Date.now()-started;
    if(error) observation.error=error.message;
    await record(action,observation);
    if(error) throw error;
    return value;
  }
  const mutations=new Set(['click','dblclick','fill','check','uncheck','setChecked','press','pressSequentially','selectOption','type']);
  const locatorBuilders=new Set(['getByLabel','getByPlaceholder','getByRole','getByTestId','getByText','locator','filter','first','last','nth','and','or','frameLocator']);
  function instrumentTab(tab) {
    function wrap(target,isTab=false) {
      return new Proxy(target,{get(object,key) {
        const value=Reflect.get(object,key);
        if(key==='playwright') return wrap(value);
        if(typeof value!=='function') return value;
        if(mutations.has(key)||(isTab&&['goto','reload','back','forward'].includes(key))) return (...args)=>run(String(key),()=>value.apply(object,args),tab);
        if(locatorBuilders.has(key)) return (...args)=>wrap(value.apply(object,args));
        if(key==='all') return async (...args)=>(await value.apply(object,args)).map(item=>wrap(item));
        return value.bind(object);
      }});
    }
    return wrap(tab,true);
  }
  return {
    scenario(id) {if(!Object.hasOwn(state.scenarios,id)) throw Error('Unknown scenario: '+id); current=id; if(state.scenarios[id].status==='NOT_RUN') state.scenarios[id].status='IN_PROGRESS'; return id;},
    record,run,instrumentTab,
    checkpoint:async tab=>record('checkpoint',await capture(tab)),
    async finish(status,evidence) {
      if(!current) throw Error('Select a scenario');
      if(!['PASS','FAIL','PARTIAL','BLOCKED'].includes(status)) throw Error('Unknown result');
      if(!evidence?.trim()) throw Error('Evidence is required for a result');
      state.scenarios[current].status=status;
      state.scenarios[current].evidence=safe(evidence);
      await saveStatus();
    }
  };
}
