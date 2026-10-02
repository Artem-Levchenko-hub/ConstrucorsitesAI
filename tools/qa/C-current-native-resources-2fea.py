"""Root's one explicit current-release C native/resource READ, no lifecycle actions."""
import hashlib, importlib.util, json, os, subprocess
from datetime import UTC, datetime
from pathlib import Path
REV='2fea1db74ba58758714063ec57be76e975fb0716'
HELPER_SHA='cd47b12332ba984a80d77d91e29af1223ce284c06aad0d0cc461233ab5c796b9'
ROOT=Path('/tmp/C-current-native-resources-2fea')
RESOURCE_CODE=r'''
import asyncio,contextlib,hashlib,io,json,logging,os
from pathlib import Path
from uuid import UUID
from datetime import datetime,timezone
async def read():
 from yleum_api.core.config import get_settings
 from yleum_api.services import orchestrator_hosts as hosts
 from yleum_api.services.project_cell_runtime import _get_cell_resources
 assert get_settings().omnia_release_sha=='2fea1db74ba58758714063ec57be76e975fb0716'
 p=Path('/tmp/qa-owned-c-post-source-fix-native-v2-b9d3e68bf459104515fb7c840cf09ae4a83ff7b84db49ddca6bbf47a70865245.json')
 x=json.loads(p.read_text());s=x['safe_summary'];ids=x['exact_scoped_identifiers_private']
 assert s['revision']=='2fea1db74ba58758714063ec57be76e975fb0716' and s['typed_quiescence_confirmed'] is True
 assert all(j['terminal_state']=='completed' and j['exit_code']==0 and j['ok'] is True for j in s['native_command_journals'])
 for key in ('owner_id','project_id','workspace_id'):
  assert hashlib.sha256(ids[key].encode()).hexdigest()==s['scope_id_hashes'][key+'_sha256']
 w=UUID(ids['workspace_id']);hosts.remember_workspace_host(w,UUID(ids['project_id']),'commerce')
 r=await asyncio.wait_for(_get_cell_resources(w),timeout=12)
 assert r.workspace_id==w
 raw={'resource':r.to_wire_json() if hasattr(r,'to_wire_json') else dict(workspace_id=str(r.workspace_id),state=r.state,draft_state=r.draft_state,preview_url=r.preview_url),'captured_at':datetime.now(timezone.utc).isoformat()}
 fd=os.open('/tmp/C-resources-2fea-private.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(raw,f)
 return dict(status='ACTUAL_ONE_OWN_C_RESOURCES_GET',revision=s['revision'],workspace_sha256=hashlib.sha256(str(w).encode()).hexdigest(),fencing_epoch=r.fencing_epoch,state=r.state,draft_state=r.draft_state,has_workspace=r.has_workspace,has_agent_home=r.has_agent_home,has_postgres=r.has_postgres,has_redis=r.has_redis,has_draft_runtime=r.has_draft_runtime,preview_url_present=bool(r.preview_url),GETs=1,POSTs=0,new_models=0,new_commands=0)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 result=asyncio.run(read())
print(json.dumps(result))
'''
def call(args,**kwargs):
    return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,**kwargs).stdout
def save(name,value):
    with os.fdopen(os.open(ROOT/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:json.dump(value,f)
def run():
    delivery=json.loads(Path('/tmp/qa-integrated-release-'+REV+'/state.json').read_text())
    assert delivery['phase']=='complete' and delivery['checks']['canonicalExitCode']==delivery['checks']['backupExitCode']==0
    helper=Path('/tmp/Cp2.py');assert hashlib.sha256(helper.read_bytes()).hexdigest()==HELPER_SHA
    spec=importlib.util.spec_from_file_location('frozen_native',helper);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    inspect=json.loads(call(['docker','inspect','yleum-prod-api']))[0]
    assert inspect['State']['Running'] is True
    cid=inspect['Id'];assert len(cid)==64
    data=json.loads(Path('/tmp/Cp2a-fresh.json').read_text())
    data.update(revision=REV,api_container_id=cid)
    data['read_ack'].update(scope_sha256=h.scope(data),issued_at_utc=datetime.now(UTC).isoformat(),production_delivered=True)
    h.validate(data);save('root-read-ack.json',data)
    call(['docker','cp',str(helper),cid+':/tmp/Cp2.py'])
    native_raw=call(['docker','exec','-i',cid,'python','-E','/tmp/Cp2.py','--execute'],input=json.dumps(data).encode())
    native=json.loads(native_raw);save('native-safe.json',native)
    assert native['typed_quiescence_confirmed'] is True and native['revision']==REV
    assert json.loads(call(['docker','inspect','yleum-prod-api']))[0]['Id']==cid
    call(['docker','cp',cid+':/tmp/qa-owned-c-post-source-fix-native-v2-'+data['new_run_sha256']+'.json',str(ROOT/'native-private.json')])
    resources=json.loads(call(['docker','exec','-i',cid,'python','-E','-'],input=RESOURCE_CODE.encode()))
    save('resources-safe.json',resources)
    call(['docker','cp',cid+':/tmp/C-resources-2fea-private.json',str(ROOT/'resources-private.json')])
    assert json.loads(call(['docker','inspect','yleum-prod-api']))[0]['Id']==cid
    save('root-runtime-binding-safe.json',{'api_container_id':cid,'runtime_inspect_running_verified':True,'same_id_before_after_both_readers':True,'actual_api_settings_release_verified':REV,'source_fix_generation_revision':'4f8bbbe6221c7836eaf794f7ee8e37d3f7be0473','normal_resources_GET_count':1,'no_lifecycle_actions':True})
    return {'status':'ACTUAL_ROOT_CURRENT_C_NATIVE_AND_RESOURCE_READ','revision':REV,'native':native,'resources':resources,'api_container_binding_verified':True}
if __name__=='__main__':
    os.umask(0o077);ROOT.mkdir(mode=0o700)
    try:result=run()
    except Exception as e:result={'status':'CURRENT_NATIVE_RESOURCE_READ_BLOCKED','error_type':type(e).__name__,'blind_replay_forbidden':True}
    save('result.json',result);print(json.dumps(result))
